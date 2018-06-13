import re
import socket
import binascii
from hashlib import md5
from typing import Iterable, Optional, Tuple, Generator, Dict
from djing.lib import safe_int
from .structs import TariffStruct, AbonStruct, IpStruct, VectorAbon, VectorTariff
from . import settings as local_settings
from django.conf import settings
from djing import ping
from agent.core import BaseTransmitter, NasNetworkError, NasFailedResult

DEBUG = getattr(settings, 'DEBUG', False)

LIST_USERS_ALLOWED = 'DjingUsersAllowed'
LIST_DEVICES_ALLOWED = 'DjingDevicesAllowed'


class ApiRos(object):
    """Routeros api"""
    sk = None
    is_login = False

    def __init__(self, ip: str, port: int):
        if self.sk is None:
            sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if port is None:
                port = local_settings.NAS_PORT
            sk.connect((ip, port or 8728))
            self.sk = sk

        self.currenttag = 0

    def login(self, username, pwd):
        if self.is_login:
            return
        chal = None
        for repl, attrs in self.talk_iter(("/login",)):
            chal = binascii.unhexlify(attrs['=ret'])
        md = md5()
        md.update(b'\x00')
        md.update(bytes(pwd, 'utf-8'))
        md.update(chal)
        for _ in self.talk_iter(("/login", "=name=" + username,
                                 "=response=00" + binascii.hexlify(md.digest()).decode('utf-8'))):
            pass
        self.is_login = True

    def talk_iter(self, words: Iterable):
        if self.write_sentence(words) == 0:
            return
        while 1:
            i = self.read_sentence()
            if len(i) == 0:
                continue
            reply = i[0]
            attrs = {}
            for w in i[1:]:
                j = w.find('=', 1)
                if j == -1:
                    attrs[w] = ''
                else:
                    attrs[w[:j]] = w[j + 1:]
            yield (reply, attrs)
            if reply == '!done':
                return

    def write_sentence(self, words: Iterable):
        ret = 0
        for w in words:
            self.write_word(w)
            ret += 1
        self.write_word('')
        return ret

    def read_sentence(self):
        r = []
        while 1:
            w = self.read_word()
            if w == '':
                return r
            r.append(w)

    def write_word(self, w):
        if DEBUG:
            print("<<< " + w)
        b = bytes(w, "utf-8")
        self.write_len(len(b))
        self.write_bytes(b)

    def read_word(self):
        ret = self.read_bytes(self.read_len()).decode('utf-8')
        if DEBUG:
            print(">>> " + ret)
        return ret

    def write_len(self, l):
        if l < 0x80:
            self.write_bytes(bytes((l,)))
        elif l < 0x4000:
            l |= 0x8000
            self.write_bytes(bytes(((l >> 8) & 0xff, l & 0xff)))
        elif l < 0x200000:
            l |= 0xC00000
            self.write_bytes(bytes(((l >> 16) & 0xff, (l >> 8) & 0xff, l & 0xff)))
        elif l < 0x10000000:
            l |= 0xE0000000
            self.write_bytes(bytes(((l >> 24) & 0xff, (l >> 16) & 0xff, (l >> 8) & 0xff, l & 0xff)))
        else:
            self.write_bytes(bytes((0xf0, (l >> 24) & 0xff, (l >> 16) & 0xff, (l >> 8) & 0xff, l & 0xff)))

    def read_len(self):
        c = self.read_bytes(1)[0]
        if (c & 0x80) == 0x00:
            pass
        elif (c & 0xC0) == 0x80:
            c &= ~0xC0
            c <<= 8
            c += self.read_bytes(1)[0]
        elif (c & 0xE0) == 0xC0:
            c &= ~0xE0
            c <<= 8
            c += self.read_bytes(1)[0]
            c <<= 8
            c += self.read_bytes(1)[0]
        elif (c & 0xF0) == 0xE0:
            c &= ~0xF0
            c <<= 8
            c += self.read_bytes(1)[0]
            c <<= 8
            c += self.read_bytes(1)[0]
            c <<= 8
            c += self.read_bytes(1)[0]
        elif (c & 0xF8) == 0xF0:
            c = self.read_bytes(1)[0]
            c <<= 8
            c += self.read_bytes(1)[0]
            c <<= 8
            c += self.read_bytes(1)[0]
            c <<= 8
            c += self.read_bytes(1)[0]
        return c

    def write_bytes(self, s):
        n = 0
        while n < len(s):
            r = self.sk.send(s[n:])
            if r == 0:
                raise NasFailedResult("connection closed by remote end")
            n += r

    def read_bytes(self, length):
        ret = b''
        while len(ret) < length:
            s = self.sk.recv(length - len(ret))
            if len(s) == 0:
                raise NasFailedResult("connection closed by remote end")
            ret += s
        return ret

    def __del__(self):
        if hasattr(self, 'sk'):
            self.sk.close()


class IpAddressListObj(IpStruct):
    __slots__ = ('__ip', 'mk_id')

    def __init__(self, ip, mk_id):
        super(IpAddressListObj, self).__init__(ip)
        self.mk_id = str(mk_id).replace('*', '')


class MikrotikTransmitter(BaseTransmitter, ApiRos):

    def __init__(self, login=None, password=None, ip=None, port=None):
        ip = ip or getattr(local_settings, 'NAS_IP')
        if ip is None or ip == '<NAS IP>':
            raise NasNetworkError('Ip address of NAS does not specified')
        if not ping(ip):
            raise NasNetworkError('NAS %(ip_addr)s does not pinged' % {
                'ip_addr': ip
            })
        try:
            super(MikrotikTransmitter, self).__init__(ip, port)
            self.login(
                login or getattr(local_settings, 'NAS_LOGIN'),
                password or getattr(local_settings, 'NAS_PASSW')
            )
        except ConnectionRefusedError:
            raise NasNetworkError('Connection to %s is Refused' % ip)

    def _exec_cmd(self, cmd: Iterable) -> Dict:
        if not isinstance(cmd, (list, tuple)):
            raise TypeError
        r = dict()
        for k, v in self.talk_iter(cmd):
            if k == '!done':
                break
            r[k] = v or None
        return r

    def _exec_cmd_iter(self, cmd: Iterable) -> Generator:
        if not isinstance(cmd, (list, tuple)):
            raise TypeError
        for k, v in self.talk_iter(cmd):
            if k == '!trap':
                raise NasFailedResult(v.get('=message'))
            if v:
                yield v

    # Build object ShapeItem from information from mikrotik
    @staticmethod
    def _build_shape_obj(info: Dict) -> AbonStruct:
        # Переводим приставку скорости Mikrotik в Mbit/s
        def parse_speed(text_speed):
            text_speed_digit = float(text_speed[:-1] or 0.0)
            text_append = text_speed[-1:]
            if text_append == 'M':
                res = text_speed_digit
            elif text_append == 'k':
                res = text_speed_digit / 1000
            # elif text_append == 'G':
            #    res = text_speed_digit * 0x400
            else:
                res = float(re.sub(r'[a-zA-Z]', '', text_speed)) / 1000 ** 2
            return res

        speeds = info['=max-limit'].split('/')
        t = TariffStruct(
            speed_in=parse_speed(speeds[1]),
            speed_out=parse_speed(speeds[0])
        )
        try:
            a = AbonStruct(
                uid=int(info['=name'][3:]),
                # FIXME: тут в разных микротиках или =target-addresses или =target
                ip=info['=target'][:-3],
                tariff=t,
                is_active=False if info['=disabled'] == 'false' else True
            )
            a.queue_id = info['=.id']
            return a
        except ValueError:
            pass

    #################################################
    #                    QUEUES
    #################################################

    # Find queue by name
    def find_queue(self, name: str) -> Optional[AbonStruct]:
        ret = self._exec_cmd(('/queue/simple/print', '?name=%s' % name))
        if len(ret) > 1:
            return self._build_shape_obj(ret[0])

    def add_queue(self, user: AbonStruct) -> None:
        if not isinstance(user, AbonStruct):
            raise TypeError
        if user.tariff is None or not isinstance(user.tariff, TariffStruct):
            return
        r = self._exec_cmd((
            '/queue/simple/add',
            '=name=uid%d' % user.uid,
            # FIXME: тут в разных микротиках или =target-addresses или =target
            '=target=%s' % user.ip,
            '=max-limit=%.3fM/%.3fM' % (user.tariff.speedOut, user.tariff.speedIn),
            '=queue=MikroBILL_SFQ/MikroBILL_SFQ',
            '=burst-time=1/1'
        ))
        print(r)

    def remove_queue(self, user: AbonStruct) -> None:
        if not isinstance(user, AbonStruct):
            raise TypeError
        q = self.find_queue('uid%d' % user.uid)
        if q is not None:
            queue_id = safe_int(getattr(q, 'queue_id'))
            if queue_id != 0:
                r = self._exec_cmd((
                    '/queue/simple/remove',
                    '=.id=%d' % queue_id
                ))
                print(r)

    def remove_queue_range(self, q_ids: Iterable[str]):
        # FIXME: check result from _exec_cmd
        r = self._exec_cmd(('/queue/simple/remove', '=numbers=' + ','.join(q_ids)))
        return r

    def update_queue(self, user: AbonStruct):
        if not isinstance(user, AbonStruct):
            raise TypeError
        if user.tariff is None:
            return
        queue = self.find_queue('uid%d' % user.uid)
        if queue is None:
            return self.add_queue(user)
        else:
            mk_id = safe_int(getattr(queue, 'queue_id', 0))
            cmd = [
                '/queue/simple/set',
                '=name=uid%d' % user.uid,
                '=max-limit=%.3fM/%.3fM' % (user.tariff.speedOut, user.tariff.speedIn),
                # FIXME: тут в разных микротиках или =target-addresses или =target
                '=target=%s' % user.ip,
                '=queue=MikroBILL_SFQ/MikroBILL_SFQ',
                '=burst-time=1/1'
            ]
            if mk_id != 0:
                cmd.insert(1, '=.id=%d' % mk_id)
            r = self._exec_cmd(cmd)
            return r

    def read_queue_iter(self) -> Generator:
        for code, dat in self._exec_cmd_iter(('/queue/simple/print', '=detail')):
            if code == '!done':
                return
            sobj = self._build_shape_obj(dat)
            if sobj is not None:
                yield sobj

    #################################################
    #         Ip->firewall->address list
    #################################################

    def add_ip(self, list_name: str, ip: IpStruct):
        if not isinstance(ip, IpStruct):
            raise TypeError
        commands = (
            '/ip/firewall/address-list/add',
            '=list=%s' % list_name,
            '=address=%s' % ip
        )
        r = self._exec_cmd(commands)
        return r

    def remove_ip(self, mk_id):
        return self._exec_cmd((
            '/ip/firewall/address-list/remove',
            '=.id=*' + str(mk_id).replace('*', '')
        ))

    def remove_ip_range(self, items: Iterable[IpAddressListObj]):
        ids = tuple(ip.mk_id for ip in items if isinstance(ip, IpAddressListObj))
        if len(ids) > 0:
            return self._exec_cmd((
                '/ip/firewall/address-list/remove',
                '=numbers=*%s' % ',*'.join(ids)
            ))

    def find_ip(self, ip: IpStruct, list_name: str):
        if not isinstance(ip, IpStruct):
            raise TypeError
        return self._exec_cmd((
            '/ip/firewall/address-list/print', 'where',
            '?list=%s' % list_name,
            '?address=%s' % ip
        ))

    def read_ips_iter(self, list_name: str) -> Generator:
        ips = self._exec_cmd_iter((
            '/ip/firewall/address-list/print', 'where',
            '?list=%s' % list_name,
            '?dynamic=no'
        ))
        for code, dat in ips:
            if dat != {}:
                yield IpAddressListObj(dat['=address'], dat['=.id'])

    #################################################
    #         BaseTransmitter implementation
    #################################################

    def add_user_range(self, user_list: VectorAbon):
        for usr in user_list:
            self.add_user(usr)

    def remove_user_range(self, users: VectorAbon):
        if not isinstance(users, (tuple, list, set)):
            raise ValueError('*users* is used twice, generator does not fit')
        queue_ids = (usr.queue_id for usr in users if usr is not None)
        self.remove_queue_range(queue_ids)
        for ip in (user.ip for user in users if isinstance(user, AbonStruct)):
            ip_list_entity = self.find_ip(ip, LIST_USERS_ALLOWED)
            if ip_list_entity is not None and len(ip_list_entity) > 1:
                self.remove_ip(ip_list_entity[0]['=.id'])

    def add_user(self, user: AbonStruct, *args):
        if not isinstance(user.ip, IpStruct):
            raise TypeError
        if user.tariff is None:
            return
        if not isinstance(user.tariff, TariffStruct):
            raise TypeError
        try:
            self.add_queue(user)
        except (NasNetworkError, NasFailedResult) as e:
            print('Error:', e)
        try:
            self.add_ip(LIST_USERS_ALLOWED, user.ip)
        except (NasNetworkError, NasFailedResult) as e:
            print('Error:', e)

    def remove_user(self, user: AbonStruct):
        self.remove_queue(user)
        firewall_ip_list_obj = self.find_ip(user.ip, LIST_USERS_ALLOWED)
        if firewall_ip_list_obj is not None and len(firewall_ip_list_obj) > 1:
            self.remove_ip(firewall_ip_list_obj[0]['=.id'])

    def update_user(self, user: AbonStruct, *args):
        if not isinstance(user.ip, IpStruct):
            raise TypeError
        find_res = self.find_ip(user.ip, LIST_USERS_ALLOWED)
        queue = self.find_queue('uid%d' % user.uid)
        if not user.is_active:
            # если не активен - то и обновлять не надо
            # но и выключить на всяк случай надо, а то вдруг был включён
            if len(find_res) > 1:
                # и если найден был - то удалим ip из разрешённых
                self.remove_ip(find_res[0]['=.id'])
            if queue is not None:
                self.remove_queue(user)
            return

        # если нет услуги то её не должно быть и в nas
        if user.tariff is None:
            if queue is not None:
                self.remove_queue(user)
            return

        # если не найден (mikrotik возвращает пустой словарь в списке если ничего нет)
        if len(find_res) < 2:
            # добавим запись об абоненте
            self.add_ip(LIST_USERS_ALLOWED, user.ip)

        # Проверяем шейпер
        if queue is None:
            self.add_queue(user)
            return
        if queue != user:
            self.update_queue(user)

    def ping(self, host, count=10) -> Optional[Tuple[int, int]]:
        r = self._exec_cmd((
            '/ip/arp/print',
            '?address=%s' % host
        ))
        if r == [{}]:
            return
        interface = r[0]['=interface']
        r = self._exec_cmd((
            '/ping', '=address=%s' % host, '=arp-ping=yes', '=interval=100ms', '=count=%d' % count,
            '=interface=%s' % interface
        ))
        received, sent = int(r[-2:][0]['=received']), int(r[-2:][0]['=sent'])
        return received, sent

    def add_tariff_range(self, tariff_list: VectorTariff):
        pass

    def remove_tariff_range(self, tariff_list: VectorTariff):
        pass

    def add_tariff(self, tariff: TariffStruct):
        pass

    def update_tariff(self, tariff: TariffStruct):
        pass

    def remove_tariff(self, tid: int):
        pass

    def read_users(self) -> Iterable[AbonStruct]:
        # shapes is ShapeItem
        allowed_ips = set(self.read_ips_iter(LIST_USERS_ALLOWED))
        queues = tuple(q for q in self.read_queue_iter() if q.ip in allowed_ips)

        ips_from_queues = set((q.ip, q) for q in queues)

        # delete ip addresses that are in firewall/address-list and there are no corresponding in queues
        diff = tuple(allowed_ips - ips_from_queues)
        if len(diff) > 0:
            self.remove_ip_range(diff)
        return queues
