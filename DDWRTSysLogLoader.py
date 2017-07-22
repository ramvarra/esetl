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
import platform
import logging

import rv.misc

#---------------------------------------------------------------------------------------------------------------
class ServerHelper:

    CITY_DB = r'/DATA/MAXMIND.GEOIP2/GeoLite2-City_20170704/GeoLite2-City.mmdb'
    ASN_DB = r'/DATA/MAXMIND.GEOIP2/GeoLite2-ASN_20170704/GeoLite2-ASN.mmdb'
    ES_HOST_CONFIG = dict(hosts=['http://192.168.1.141:9200'], timeout=240)

    #--------------------------------------------------------------------------------------
    def __init__(self):
        self.pat1 = re.compile(r"<4>(?P<dt>\w+\s+\d+\s+\d+:\d+:\d+)\s+kernel:\s+(?P<action>[A-Z]+)\s+(?P<opts>.*)")
        self.pat2 = r'<(?P<code>\d+)>(?P<dt>\w+\s+\d+\s+\d+:\d+:\d+)\s+(?P<fac>[^:]+):\s+(?P<rest>.*)'
        self.local_tz = tzlocal.get_localzone()
        self.gi = GeoIP.new(GeoIP.GEOIP_MEMORY_CACHE)
        self.city_reader = geoip2.database.Reader(self.CITY_DB)
        self.asn_reader = geoip2.database.Reader(self.ASN_DB)
        self.es = elasticsearch.Elasticsearch(**self.ES_HOST_CONFIG)
        logging.info("Connecting to Elasticsearch: {}".format(self.ES_HOST_CONFIG['hosts']))

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

    # -------------------------------------------------------------------------------------------------------------------
    def create_templates(self):
        rl_props = {
            "SRC_LOC": { "type": "geo_point"},
            "DST_LOC": { "type": "geo_point"},
            "SRC": {"type": "ip"},
            "DST": {"type": "ip"},
            "TS": {"type": "date"},
        }

        self.setup_template('routerlog', rl_props)

        syslog_props = {
            "TS": {"type": "date"},
        }
        self.setup_template('router_syslog', syslog_props)

    #-------------------------------------------------------------------------------------------------------------------
    def setup_template(self, doc_type, props, settings=None):
        template_name = doc_type + '_template'
        template_pattern = doc_type + '-*'
        if settings is None:
            settings = {
                "number_of_shards": 2,
                "number_of_replicas": 0
            }

        template = {
            "template": template_pattern,
            "settings": settings,
            "mappings": {
                doc_type: {
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
                    "properties": props
                }
            }
        }


        try:
            self.es.indices.delete_template(template_name)
        except:
            pass
        logging.info('Creating template - {}'.format(template_name))

        ret = self.es.indices.put_template(template_name, template)
        logging.info("Ret: {}".format(ret))


    # ------------------------------------------------------------------------------------------
    def index(self, doc_type, rec):
        ix = '{}-{:%Y.%W}'.format(doc_type, rec['TS'])
        self.es.index(ix, doc_type, body=rec)

    # ------------------------------------------------------------------------------------------
    def get_ts(self, ts_string):
        ts = datetime.datetime.strptime(ts_string, '%b %d %H:%M:%S')
        ts = self.local_tz.localize(ts.replace(year=datetime.date.today().year))
        return ts

    # ------------------------------------------------------------------------------------------
    def get_fac(self, fac):
        fac_n = ''
        if '[' in fac:
            fac, fac_n = fac.split('[')
            fac_n = fac_n.rstrip(']')
        return (fac, fac_n)

#------------------------------------------------------------------------------------------
class SyslogReceiver(socketserver.BaseRequestHandler):

    # --------------------------------------------------------------------------------------
    def handle(self):
        global server_helper

        data = self.request[0].strip().decode()
        m = re.match(server_helper.pat1, data)
        if m:
            ts = server_helper.get_ts(m.group('dt'))
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
            #logging.info("Writing {}".format(d))
            server_helper.index('routerlog', d)
            return

        m = re.match(server_helper.pat2, data)
        if m:
            d = {'TS': server_helper.get_ts(m.group('dt'))}
            d['CODE'] = m.group('code')
            fac, fac_n = server_helper.get_fac(m.group('fac'))
            d['FAC'] = fac
            if fac_n:
                d['FAC_N'] = fac_n
            d['MSG'] = m.group('rest')
            #logging.info("Writing {}".format(d))
            server_helper.index('router_syslog', d)
            return



        logging.warning("*UNKNOWN*: {}".format(data))



#-------------------------------------------------------------------------------------------
if __name__ == '__main__':
    HOST, PORT = platform.node(), 514
    rv.misc.set_logging(log_dir='/var/log/esetl')

    server_helper = ServerHelper()
    if len(sys.argv) == 2:
        assert sys.argv[1] == 'initialize_template'
        server_helper.create_templates()
        sys.exit(1)

    with socketserver.UDPServer((HOST, PORT), SyslogReceiver) as server:
        logging.info('Running on {}'.format((HOST, PORT)))
        server.serve_forever()