#!/usr/local/anaconda3/bin/python
import os
import sys
import re
import socketserver
import tzlocal
import elasticsearch
import datetime
import ipaddress
import platform
import logging
import socket

import rv.misc
import rv.geoip
import rv.ESUtil

#---------------------------------------------------------------------------------------------------------------
class ServerHelper:
    ES_CONFIG = dict(hosts='http://192.168.1.141:9200', timeout=240)

    OPTS = ['SYN', 'ACK', 'CODE', 'FIN', 'RST', 'SRC', 'DST', 'SPT', 'DPT', 'MAC', 'TOS', 'TTL', 'TYPE', 'WINDOW', 'LEN',
            'ECE', 'ID', 'IN', 'MARK', 'OUT', 'OPT', 'PREC', 'PROTO', 'SEQ', 'URG', 'URGP',]
    #--------------------------------------------------------------------------------------
    def __init__(self):
        self.pat1 = re.compile(r"<4>(?P<dt>\w+\s+\d+\s+\d+:\d+:\d+)\s+kernel:\s+(?P<action>[A-Z]+)\s+(?P<opts>.*)")
        self.pat2 = r'<(?P<code>\d+)>(?P<dt>\w+\s+\d+\s+\d+:\d+:\d+)\s+(?P<fac>[^:]+):\s+(?P<rest>.*)'
        self.local_tz = tzlocal.get_localzone()
        self.esu = rv.ESUtil.ESUtil(**self.ES_CONFIG)
        self.es = self.esu._es
        self.geoip = rv.geoip.GeoIP()

        logging.info("Connecting to Elasticsearch: {}".format(self.ES_CONFIG))

    # -------------------------------------------------------------------------------------------------------------------
    def create_templates(self):
        routerlog_props = {
            "SRC_LOC": { "type": "geo_point"},
            "DST_LOC": { "type": "geo_point"},
            "SRC": {"type": "ip"},
            "DST": {"type": "ip"},
            "TS": {"type": "date"},
        }
        syslog_props = {
            "TS": {"type": "date"},
        }

        self.esu.create_template('routerlog_template', 'routerlog-*', routerlog_props)
        self.esu.create_template('router_syslog_template', 'router_syslog-*', syslog_props)


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
            flags = []
            #logging.info('OPTS = {}'.format(m.group('opts')))
            for opt in m.group('opts').split():
                k, v = opt.split('=') if '=' in opt else (opt, '')
                if k in server_helper.OPTS:
                    d[k] = v if v else 'Y'
                else:
                    flags.append((k, v))

            d['FLAGS'] = ' '.join('{}{}{}'.format(f, '=' if v else '', v) for f,v in flags)

            for c in ('SRC', 'DST'):
                if c in d:
                    gip = server_helper.geoip.lookup(d[c])
                    d.update({"{}_{}".format(c, k.upper()): v for k, v in gip.items()})

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
    LOG_DIR = '/var/log/esetl'
    rv.misc.set_logging(LOG_DIR)

    try:
        server_helper = ServerHelper()
        if len(sys.argv) == 2:
            assert sys.argv[1] == 'initialize_template', "Usage: {} [initialize_template]".format(os.path.basename(sys.argv[0]))
            server_helper.create_templates()
            sys.exit(1)

        with socketserver.UDPServer((HOST, PORT), SyslogReceiver) as server:
            logging.info('Running on {}'.format((HOST, PORT)))
            server.serve_forever()
    except Exception as ex:
        logging.exception("Server Failed with Exception")
