# -*- coding: utf-8 -*-
from django.core.exceptions import MultipleObjectsReturned
from abonapp.models import Abon
from devapp.models import Device, Port


def dhcp_commit(client_ip, client_mac, switch_mac, switch_port):
    try:
        dev = Device.objects.get(mac_addr=switch_mac)
        mngr_class = dev.get_manager_klass()

        if mngr_class.is_use_device_port():
            port = Port.objects.get(device=dev, num=switch_port)
            abon = Abon.objects.get(dev_port=port, device=dev)
        else:
            abon = Abon.objects.get(device=dev)
        if not abon.is_dynamic_ip:
            print('D:', 'User settings is not dynamic')
            return
        if not abon.is_access():
            print('D:', 'User %s is not access to service' % abon.username)
            return
        abon.ip_address = client_ip
        abon.save(update_fields=['ip_address'])
        abon.sync_with_nas(created=False)
    except Abon.DoesNotExist:
        print('N:', "User with device '%s' does not exist" % dev)
    except Device.DoesNotExist:
        print('N:', 'Device with mac %s not found' % switch_mac)
    except Port.DoesNotExist:
        print('N:', 'Port %(switch_port)d on device with mac %(switch_mac)s does not exist' % {
            'switch_port': int(switch_port),
            'switch_mac': switch_mac
        })
    except MultipleObjectsReturned as e:
        print('E:', 'MultipleObjectsReturned:', type(e), e, port, dev)


def dhcp_expiry(client_ip):
    try:
        abon = Abon.objects.get(ip_address=client_ip)
        abon.ip_address = None
        abon.save(update_fields=['ip_address'])
        abon.sync_with_nas(created=False)
    except Abon.DoesNotExist:
        pass


def dhcp_release(client_ip):
    dhcp_expiry(client_ip)
