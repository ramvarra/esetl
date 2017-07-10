import os
import sys
import re
import socketserver
import tzlocal
import elasticsearch
import datetime
import GeoIP
import geoip2.database
import ipaddress


#---------------------------------------------------------------------------------------------------------------
class ServerHelper:

    CITY_DB = r'/DATA/MAXMIND.GEOIP2/GeoLite2-City_20170704/GeoLite2-City.mmdb'
    ASN_DB = r'/DATA/MAXMIND.GEOIP2/GeoLite2-ASN_20170704/GeoLite2-ASN.mmdb'

    #--------------------------------------------------------------------------------------
    def __init__(self):
        print("Receiver: init")
        self.pat = re.compile(r"<4>(?P<dt>\w+\s+\d+\s+\d+:\d+:\d+)\s+kernel:\s+(?P<action>[A-Z]+)\s+(?P<opts>.*)")
        self.local_tz = tzlocal.get_localzone()
        self.gi = GeoIP.new(GeoIP.GEOIP_MEMORY_CACHE)
        self.city_reader = geoip2.database.Reader(self.CITY_DB)
        self.asn_reader = geoip2.database.Reader(self.ASN_DB)
        self.es = elasticsearch.Elasticsearch(hosts=['http://localhost:9200'], timeout=240)
        print("Receiver: init done")

    # --------------------------------------------------------------------------------------
    def geo_ip(self, ip):
        r = {}

        if ip.startswith('192.168.') or ip.startswith('10.') or ipaddress.ip_address(ip).is_private:
            return r

        try:
            asn = self.asn_reader.asn(ip)
            r['ORG'] = asn.autonomous_system_organization
        except geoip2.errors.AddressNotFoundError:
            pass

        try:
            city = self.city_reader.city(ip)
            r['CITY'] = city.city.name
            r['COUNTRY'] = city.country.name
            r['LOC'] = {'lat': city.location.latitude, 'lon': city.location.longitude}
        except geoip2.errors.AddressNotFoundError:
            pass
        return r

    def setup_template(self):
        template = {
            "template": "routerlog-*",
            "settings": {
                "number_of_shards": 2,
                "number_of_replicas": 0
            },
            "mappings": {
                "routerlog": {
                    "_all": {
                        "enabled": False
                    },
                    "dynamic_templates": [
                        {
                            "strings": {
                                "match_mapping_type": "string",
                                "mapping": {
                                    "type": "keyword"
                                }
                            }
                        }
                    ],
                    "properties": {
                        "SRC_LOC": {
                            "type": "geo_point"
                        },
                        "DST_LOC": {
                            "type": "geo_point"
                        },
                        "SRC": {
                            "type": "ip"
                        },
                        "DST": {
                            "type": "ip"
                        },
                        "TS": {
                            "type": "date"
                        }
                    }
                }
            }
        }


        try:
            self.es.indices.delete_template('routerlog_template')
        except:
            pass
        print('Creating template')
        self.es.indices.put_template('routerlog_template', template)

    def index(self, rec):
        ix = 'routerlog-{:%Y.%W}'.format(rec['TS'])
        self.es.index(ix, doc_type = 'routerlog', body=rec)

#------------------------------------------------------------------------------------------
class SyslogReceiver(socketserver.BaseRequestHandler):
    # --------------------------------------------------------------------------------------
    def handle(self):
        global server_helper

        data = self.request[0].strip()
        m = re.match(server_helper.pat, data.decode())
        if m:
            ts = datetime.datetime.strptime(m.group('dt'), '%b %d %H:%M:%S')
            ts = server_helper.local_tz.localize(ts.replace(year=datetime.date.today().year))
            action = m.group('action')
            d = {'TS': ts, 'ACTION': action}
            for opt in m.group('opts').split():
                if '=' not in opt:
                    opt += '=Y'
                k, v = opt.split('=')
                d[k] = v
            src = server_helper.geo_ip(d['SRC'])
            d.update({"SRC_{}".format(k): v for k,v in src.items()})
            dst = server_helper.geo_ip(d['DST'])
            d.update({"DST_{}".format(k): v for k, v in dst.items()})
            server_helper.index(d)
        else:
            print("*UNKNOWN*: {}".format(data))


#-------------------------------------------------------------------------------------------
if __name__ == '__main__':
    HOST, PORT = 'UBU01', 514
    server_helper = ServerHelper()
    if len(sys.argv) == 2:
        assert sys.argv[1] == 'initialize_template'
        server_helper.setup_template()
        sys.exit(1)

    with socketserver.UDPServer((HOST, PORT), SyslogReceiver) as server:
        print('Running on ', (HOST, PORT))
        server.serve_forever()