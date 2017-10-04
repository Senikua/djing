# -*- coding: utf-8 -*-
# Generated by Django 1.9 on 2017-09-27 18:38
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('abonapp', '0002_auto_20170905_1248'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='abon',
            options={'permissions': (('can_buy_tariff', 'Покупка тарифа абоненту'), ('can_view_passport', 'Может просматривать паспортные данные'), ('can_add_ballance', 'Пополнение счёта'), ('can_ping', 'Может пинговать')), 'verbose_name': 'Абонент', 'verbose_name_plural': 'Абоненты'},
        ),
        migrations.AlterModelOptions(
            name='abongroup',
            options={'permissions': (('can_view_abongroup', 'Может просматривать группу абонентов'),), 'verbose_name': 'Группа абонентов', 'verbose_name_plural': 'Группы абонентов'},
        ),
        migrations.AlterModelOptions(
            name='abonlog',
            options={'permissions': (('can_view_abonlog', 'Может видеть логи абонента'),)},
        ),
        migrations.AlterModelOptions(
            name='additionaltelephone',
            options={'ordering': ('owner_name',), 'permissions': (('can_view_additionaltelephones', 'Может видеть дополнительные телефоны'),), 'verbose_name': 'Дополнительный телефон', 'verbose_name_plural': 'Дополнительные телефоны'},
        ),
        migrations.AlterModelOptions(
            name='invoiceforpayment',
            options={'ordering': ('date_create',), 'permissions': (('can_view_invoiceforpayment', 'Может видеть назначенные платежи'),), 'verbose_name': 'Квитанция (долг)', 'verbose_name_plural': 'Квитанции (долги)'},
        ),
        migrations.AlterModelOptions(
            name='passportinfo',
            options={'verbose_name': 'Паспортные данные', 'verbose_name_plural': 'Паспортные данные'},
        ),
        migrations.AlterModelTable(
            name='passportinfo',
            table='passport_info',
        ),
    ]
