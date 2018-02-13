from datetime import datetime

from django.conf import settings
from django.core import validators
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, connection, transaction
from django.db.models.signals import post_save, post_delete, pre_delete, post_init
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from accounts_app.models import UserProfile, MyUserManager
from agent import Transmitter, AbonStruct, TariffStruct, NasFailedResult, NasNetworkError
from mydefs import MyGenericIPAddressField, ip2int, LogicError, ip_addr_regex
from tariff_app.models import Tariff, PeriodicPay
from bitfield import BitField


TELEPHONE_REGEXP = getattr(settings, 'TELEPHONE_REGEXP', r'^\+[7,8,9,3]\d{10,11}$')


class AbonGroup(models.Model):
    title = models.CharField(max_length=127, unique=True)
    profiles = models.ManyToManyField(UserProfile, blank=True, related_name='abon_groups')
    tariffs = models.ManyToManyField(Tariff, blank=True, related_name='tariff_groups')

    class Meta:
        db_table = 'abonent_groups'
        permissions = (
            ('can_view_abongroup', _('Can view subscriber group')),
        )
        verbose_name = _('Abon group')
        verbose_name_plural = _('Abon groups')
        ordering = ['title']

    def __str__(self):
        return self.title


class AbonLog(models.Model):
    abon = models.ForeignKey('Abon', models.CASCADE)
    amount = models.FloatField(default=0.0)
    author = models.ForeignKey(UserProfile, models.CASCADE, related_name='+', blank=True, null=True)
    comment = models.CharField(max_length=128)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'abonent_log'
        permissions = (
            ('can_view_abonlog', _('Can view subscriber logs')),
        )
        ordering = ['-date']

    def __str__(self):
        return self.comment


class AbonTariff(models.Model):
    tariff = models.ForeignKey(Tariff, models.CASCADE, related_name='linkto_tariff')

    time_start = models.DateTimeField(null=True, blank=True, default=None)

    deadline = models.DateTimeField(null=True, blank=True, default=None)

    def calc_amount_service(self):
        amount = self.tariff.amount
        return round(amount, 2)

    # is used service now, if time start is present than it activated
    def is_started(self):
        return False if self.time_start is None else True

    def __str__(self):
        return "%s: %s" % (
            self.deadline,
            self.tariff.title
        )

    class Meta:
        db_table = 'abonent_tariff'
        permissions = (
            ('can_complete_service', _('finish service perm')),
        )
        verbose_name = _('Abon service')
        verbose_name_plural = _('Abon services')
        ordering = ['time_start']


class AbonStreet(models.Model):
    name = models.CharField(max_length=64)
    group = models.ForeignKey(AbonGroup, models.CASCADE)

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'abon_street'
        verbose_name = _('Street')
        verbose_name_plural = _('Streets')
        ordering = ['name']


class ExtraFieldsModel(models.Model):
    DYNAMIC_FIELD_TYPES = (
        ('int', _('Digital field')),
        ('str', _('Text field')),
        ('dbl', _('Floating field')),
        ('ipa', _('Ip Address'))
    )

    title = models.CharField(max_length=16, default='no title')
    field_type = models.CharField(max_length=3, choices=DYNAMIC_FIELD_TYPES, default='str')
    data = models.CharField(max_length=64, null=True, blank=True)

    def get_regexp(self):
        if self.field_type == 'int':
            return r'^[+-]?\d+$'
        elif self.field_type == 'dbl':
            return r'^[-+]?\d+[,.]\d+$'
        elif self.field_type == 'str':
            return r'^[a-zA-ZА-Яа-я0-9]+$'
        elif self.field_type == 'ipa':
            return ip_addr_regex

    def clean(self):
        d = self.data
        if self.field_type == 'int':
            validators.validate_integer(d)
        elif self.field_type == 'dbl':
            try:
                float(d)
            except ValueError:
                raise ValidationError(_('Double invalid value'), code='invalid')
        elif self.field_type == 'str':
            str_validator = validators.MaxLengthValidator(64)
            str_validator(d)

    def __str__(self):
        return "%s: %s" % (self.get_field_type_display(), self.data)

    class Meta:
        db_table = 'abon_extra_fields'


class AbonManager(MyUserManager):

    def get_queryset(self):
        return super(MyUserManager, self).get_queryset().filter(is_admin=False)


class Abon(UserProfile):
    current_tariff = models.ForeignKey(AbonTariff, null=True, blank=True, on_delete=models.SET_NULL)
    group = models.ForeignKey(AbonGroup, models.SET_NULL, blank=True, null=True, verbose_name=_('User group'))
    ballance = models.FloatField(default=0.0)
    ip_address = MyGenericIPAddressField(blank=True, null=True)
    description = models.TextField(_('Comment'), null=True, blank=True)
    street = models.ForeignKey(AbonStreet, on_delete=models.SET_NULL, null=True, blank=True, verbose_name=_('Street'))
    house = models.CharField(_('House'), max_length=12, null=True, blank=True)
    extra_fields = models.ManyToManyField(ExtraFieldsModel, blank=True)
    device = models.ForeignKey('devapp.Device', null=True, blank=True, on_delete=models.SET_NULL)
    dev_port = models.ForeignKey('devapp.Port', null=True, blank=True, on_delete=models.SET_NULL)
    is_dynamic_ip = models.BooleanField(default=False)

    MARKER_FLAGS = (
        ('icon_donkey', _('Donkey')),
        ('icon_fire', _('Fire')),
        ('icon_ok', _('Ok')),
        ('icon_king', _('King')),
        ('icon_tv', _('TV')),
        ('icon_smile', _('Smile')),
        ('icon_dollar', _('Dollar')),
        ('icon_service', _('Service')),
        ('icon_mrk', _('Marker'))
    )
    markers = BitField(flags=MARKER_FLAGS, default=0)

    def get_flag_icons(self):
        """
        Return icon list of set flags from self.markers
        :return: ['m-icon-donkey', 'm-icon-tv', ...]
        """
        return ["m-%s" % name for name, state in self.markers if state]

    def is_markers_empty(self):
        return int(self.markers) == 0

    def active_tariff(self):
        return self.current_tariff

    objects = AbonManager()

    class Meta:
        db_table = 'abonent'
        permissions = (
            ('can_buy_tariff', _('Buy service perm')),
            ('can_view_passport', _('Can view passport')),
            ('can_add_ballance', _('fill account')),
            ('can_ping', _('Can ping'))
        )
        # TODO: Fix when duplicates already in database
        #unique_together = ('device', 'dev_port')
        verbose_name = _('Abon')
        verbose_name_plural = _('Abons')
        ordering = ['fio']

    # pay something
    def make_pay(self, curuser, how_match_to_pay=0.0):
        post_save.disconnect(abon_post_save, sender=Abon)
        self.ballance -= how_match_to_pay
        self.save(update_fields=['ballance'])
        post_save.connect(abon_post_save, sender=Abon)

    def add_ballance(self, current_user, amount, comment):
        AbonLog.objects.create(
            abon=self,
            amount=amount,
            author=current_user,
            comment=comment
        )
        self.ballance += amount

    def pick_tariff(self, tariff, author, comment=None, deadline=None):
        if not isinstance(tariff, Tariff):
            raise TypeError

        amount = round(tariff.amount, 2)

        if not author.is_staff and tariff.is_admin:
            raise LogicError(_('User that is no staff can not buy admin services'))

        if self.current_tariff is not None:
            if self.current_tariff.tariff == tariff:
                # if service already connected
                raise LogicError(_('That service already activated'))
            else:
                # if service is present then speak about it
                raise LogicError(_('Service already activated'))

        # if not enough money
        if self.ballance < amount:
            raise LogicError(_('not enough money'))

        new_abtar = AbonTariff(deadline=deadline, tariff=tariff)
        new_abtar.save()
        self.current_tariff = new_abtar

        # charge for the service
        self.ballance -= amount

        self.save()

        # make log about it
        AbonLog.objects.create(
            abon=self, amount=-tariff.amount,
            author=author,
            comment=comment or _('Buy service default log')
        )

    # Destroy the service if the time has come
    def bill_service(self, author):
        abon_tariff = self.active_tariff()
        if abon_tariff is None:
            return
        nw = timezone.now()
        # if service is overdue
        if nw > abon_tariff.deadline:
            print("Service %s for user %s is overdued, end service" % (abon_tariff.tariff, self))
            abon_tariff.delete()

    # is subscriber have access to service, view in tariff_app.custom_tariffs.<TariffBase>.manage_access()
    def is_access(self):
        abon_tariff = self.active_tariff()
        if abon_tariff is None:
            return False
        trf = abon_tariff.tariff
        ct = trf.get_calc_type()(abon_tariff)
        return ct.manage_access(self)

    # make subscriber from agent structure
    def build_agent_struct(self):
        if self.ip_address:
            user_ip = ip2int(self.ip_address)
        else:
            return
        abon_tariff = self.active_tariff()
        if abon_tariff is None:
            agent_trf = None
        else:
            trf = abon_tariff.tariff
            agent_trf = TariffStruct(trf.id, trf.speedIn, trf.speedOut)
        return AbonStruct(self.pk, user_ip, agent_trf, bool(self.is_active))

    def save(self, *args, **kwargs):
        # check if ip address already busy
        if self.ip_address is not None and Abon.objects.filter(ip_address=self.ip_address).exclude(pk=self.pk).count() > 0:
            self.is_bad_ip = True
            raise LogicError(_('Ip address already exist'))
        super(Abon, self).save(*args, **kwargs)


class PassportInfo(models.Model):
    series = models.CharField(max_length=4, validators=[validators.integer_validator])
    number = models.CharField(max_length=6, validators=[validators.integer_validator])
    distributor = models.CharField(max_length=64)
    date_of_acceptance = models.DateField()
    abon = models.OneToOneField(Abon, on_delete=models.SET_NULL, blank=True, null=True)

    class Meta:
        db_table = 'passport_info'
        verbose_name = _('Passport Info')
        verbose_name_plural = _('Passport Info')

    def __str__(self):
        return "%s %s" % (self.series, self.number)


class InvoiceForPayment(models.Model):
    abon = models.ForeignKey(Abon, models.CASCADE)
    status = models.BooleanField(default=False)
    amount = models.FloatField(default=0.0)
    comment = models.CharField(max_length=128)
    date_create = models.DateTimeField(auto_now_add=True)
    date_pay = models.DateTimeField(blank=True, null=True)
    author = models.ForeignKey(UserProfile, related_name='+', on_delete=models.SET_NULL, blank=True, null=True)

    def __str__(self):
        return "%s -> %.2f" % (self.abon.username, self.amount)

    def set_ok(self):
        self.status = True
        self.date_pay = timezone.now()

    def get_prev_invoice(self):
        return self.objects.order

    class Meta:
        ordering = ('date_create',)
        db_table = 'abonent_inv_pay'
        permissions = (
            ('can_view_invoiceforpayment', _('Can view invoice for payment')),
        )
        verbose_name = _('Debt')
        verbose_name_plural = _('Debts')


class AllTimePayLogManager(models.Manager):
    @staticmethod
    def by_days():
        cur = connection.cursor()
        cur.execute(r'SELECT SUM(summ) as alsum, DATE_FORMAT(date_add, "%Y-%m-%d") AS pay_date FROM  all_time_pay_log '
                    r'GROUP BY DATE_FORMAT(date_add, "%Y-%m-%d")')
        while True:
            r = cur.fetchone()
            if r is None: break
            summ, dat = r
            print(summ, dat)
            yield {'summ': summ, 'pay_date': datetime.strptime(dat, '%Y-%m-%d')}


# Log for pay system "AllTime"
class AllTimePayLog(models.Model):
    abon = models.ForeignKey(Abon, models.SET_DEFAULT, blank=True, null=True, default=None)
    pay_id = models.CharField(max_length=36, unique=True, primary_key=True)
    date_add = models.DateTimeField(auto_now_add=True)
    summ = models.FloatField(default=0.0)
    trade_point = models.CharField(_('Trade point'), max_length=20, default=None, null=True, blank=True)
    receipt_num = models.BigIntegerField(_('Receipt number'), default=0)

    objects = AllTimePayLogManager()

    def __str__(self):
        return self.pay_id

    class Meta:
        db_table = 'all_time_pay_log'
        ordering = ['-date_add']


# log for all terminals
class AllPayLog(models.Model):
    pay_id = models.CharField(max_length=64, primary_key=True)
    date_action = models.DateTimeField(auto_now_add=True)
    summ = models.FloatField(default=0.0)
    pay_system_name = models.CharField(max_length=16)

    def __str__(self):
        return self.pay_system_name

    class Meta:
        db_table = 'all_pay_log'
        ordering = ['-date_action']


class AbonRawPassword(models.Model):
    account = models.OneToOneField(Abon, models.CASCADE, primary_key=True)
    passw_text = models.CharField(max_length=64)

    def __str__(self):
        return "%s - %s" % (self.account, self.passw_text)

    class Meta:
        db_table = 'abon_raw_password'


class AdditionalTelephone(models.Model):
    abon = models.ForeignKey(Abon, models.CASCADE, related_name='additional_telephones')
    telephone = models.CharField(
        max_length=16,
        verbose_name=_('Telephone'),
        # unique=True,
        validators=[RegexValidator(TELEPHONE_REGEXP)]
    )
    owner_name = models.CharField(max_length=127)

    def __str__(self):
        return "%s - (%s)" % (self.owner_name, self.telephone)

    class Meta:
        db_table = 'additional_telephones'
        ordering = ('owner_name',)
        permissions = (
            ('can_view_additionaltelephones', _('Can view additional telephones')),
        )
        verbose_name = _('Additional telephone')
        verbose_name_plural = _('Additional telephones')


class PeriodicPayForId(models.Model):
    periodic_pay = models.ForeignKey(PeriodicPay, models.CASCADE, verbose_name=_('Periodic pay'))
    last_pay = models.DateTimeField(_('Last pay time'), blank=True, null=True)
    next_pay = models.DateTimeField(_('Next time to pay'))
    account = models.ForeignKey(Abon, models.CASCADE, verbose_name=_('Account'))

    def payment_for_service(self, author=None, comment=None):
        """
        Charge for the service and leave a log about it
        """
        now = timezone.now()
        if self.next_pay < now:
            pp = self.periodic_pay
            amount = pp.calc_amount()
            next_pay_date = pp.get_next_time_to_pay(self.last_pay)
            abon = self.account
            with transaction.atomic():
                abon.make_pay(author, amount)
                AbonLog.objects.create(
                    abon=abon, amount=-amount,
                    author=author,
                    comment=comment or _('Charge for "%(service)s"') % {
                        'service': self.periodic_pay
                    }
                )
                self.last_pay = now
                self.next_pay = next_pay_date
                self.save(update_fields=['last_pay', 'next_pay'])

    def __str__(self):
        return "%s %s" % (self.periodic_pay, self.next_pay)

    class Meta:
        db_table = 'periodic_pay_for_id'


@receiver(post_save, sender=Abon)
def abon_post_save(sender, **kwargs):
    instance, created = kwargs["instance"], kwargs["created"]
    timeout = None
    if hasattr(instance, 'is_dhcp') and instance.is_dhcp:
        timeout = getattr(settings, 'DHCP_TIMEOUT', 14400)
    agent_abon = instance.build_agent_struct()
    if agent_abon is None:
        return True
    try:
        tm = Transmitter()
        if created:
            tm.add_user(agent_abon, ip_timeout=timeout)
        else:
            tm.update_user(agent_abon, ip_timeout=timeout)

    except (NasFailedResult, NasNetworkError, ConnectionResetError) as e:
        print('ERROR:', e)
        return True


@receiver(post_delete, sender=Abon)
def abon_del_signal(sender, **kwargs):
    abon = kwargs["instance"]
    try:
        ab = abon.build_agent_struct()
        if ab is None:
            return True
        tm = Transmitter()
        tm.remove_user(ab)
    except (NasFailedResult, NasNetworkError):
        return True


@receiver(post_init, sender=AbonTariff)
def abon_tariff_post_init(sender, **kwargs):
    abon_tariff = kwargs["instance"]
    if getattr(abon_tariff, 'time_start') is None:
        abon_tariff.time_start = timezone.now()
    calc_obj = abon_tariff.tariff.get_calc_type()(abon_tariff)
    if getattr(abon_tariff, 'deadline') is None:
        abon_tariff.deadline = calc_obj.calc_deadline()


@receiver(pre_delete, sender=AbonTariff)
def abontariff_pre_delete(sender, **kwargs):
    abon_tariff = kwargs["instance"]
    try:
        abon = Abon.objects.get(current_tariff=abon_tariff)
        ab = abon.build_agent_struct()
        if ab is None:
            return True
        tm = Transmitter()
        tm.remove_user(ab)
    except Abon.DoesNotExist:
        print('ERROR: Abon.DoesNotExist')
    except (NasFailedResult, NasNetworkError, ConnectionResetError) as e:
        print('NetErr:', e)
        return True
