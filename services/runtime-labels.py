#!/usr/bin/python3
"""Read-only Waydroid platform catalog over the worker's private Binder device."""
import json
import sys


def catalog(reader):
    def integer():
        ok, value = reader.read_int32()
        if not ok: raise ValueError('Incomplete platform reply')
        return value
    def string():
        value = reader.read_string16()
        if value is not None and (not isinstance(value, str) or len(value) > 4096):
            raise ValueError('Invalid platform string')
        return value or ''
    if integer() != 0: raise ValueError('Platform exception')
    count = integer()
    if not 0 <= count <= 10000: raise ValueError('Invalid catalog size')
    labels = {}
    for _ in range(count):
        if integer() == 0: continue
        label, package = string(), string()
        for _ in range(4): string()  # action, intent, component package/class
        categories = integer()
        if not 0 <= categories <= 1000: raise ValueError('Invalid category count')
        for _ in range(categories): string()
        if label.strip(): labels.setdefault(package, label.strip()[:512])
    return labels


def main():
    import gbinder
    manager = gbinder.ServiceManager(sys.argv[1], 'aidl3', 'aidl3')
    if not manager.is_present(): raise RuntimeError('Private Binder manager unavailable')
    remote, status = manager.get_service_sync('waydroidplatform')
    if status or not remote: raise RuntimeError('Waydroid platform unavailable')
    client = gbinder.Client(remote, 'lineageos.waydroid.IPlatform')
    reply, status = client.transact_sync_reply(3, client.new_request())
    if status or not reply: raise RuntimeError('Platform catalog request failed')
    print(json.dumps(catalog(reply.init_reader()), ensure_ascii=False))


if __name__ == '__main__': main()
