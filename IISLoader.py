import os, re, sys
import elasticsearch
import datetime
import pytz
import tzlocal
import httpagentparser
import logging
import itertools
import rv.misc
import rv.geoip
import rv.ESUtil


class IISLogReader:
    COLFMT = 's-ip cs-method cs-uri-stem cs-uri-query s-port cs-username c-ip cs(User-Agent) cs(Referer) sc-status sc-substatus sc-win32-status time-taken'
    ES_CONFIG = dict(hosts='http://192.168.1.141:9200', timeout=240)
    INDEX_PREFIX = 'iislog'
    IIS_LOG_DIR = r'C:\inetpub\logs\LogFiles\W3SVC1'

    def __init__(self):
        self.regex = r'u_ex(?P<dt>\d+)\.log'
        self._cols = [re.sub(r'[-\(\)]', '_', c).lower().rstrip('_') for c in self.COLFMT.split()]
        self.local_tz = tzlocal.get_localzone()
        self.geoip = rv.geoip.GeoIP()
        self.esu = rv.ESUtil.ESUtil(**self.ES_CONFIG)
        self.total_count = 0

    def get_ua(self, uas):
        '''
        generate flat list of UA attributes
        :param uas: ua string from client
        :return: flat dict of detected ua attributes.
        '''
        d = httpagentparser.detect(uas)
        r = {}

        for k, v in d.items():
            if isinstance(v, dict):
                for k1, v1 in v.items():
                    if v1:
                        r[k + '_' + k1] = v1
            else:
                r[k] = v
        if not r:
            r['browser_name'] = uas
        return r

    def _get_dt(self, f):
        if f is None:
            return None

        m = re.match(self.regex, f)
        if m:
            dts = m.group('dt')
            dt = datetime.date(int(dts[:2]) + 2000, int(dts[2:4]), int(dts[4:]))
            return dt
        else:
            return None

    def get_file_entry(self, last_file=None):
        flist = []
        for f in os.listdir(self.IIS_LOG_DIR):
            dt = self._get_dt(f)
            if dt:
                d = {'date': dt, 'file': f}
                flist.append(d)

        # sort files
        flist = sorted(flist, key=lambda x: x['date'])
        last_file_dt = self._get_dt(last_file)
        for entry in flist:
            if last_file_dt and entry['date'] < last_file_dt:
                continue
            yield entry

    def getrec(self, last_file=None, last_offset=-1):
        self.total_count = 0
        for entry in self.get_file_entry(last_file):
            full_f = os.path.join(self.IIS_LOG_DIR, entry['file'])
            logging.info('Processing file: {} offset: {}'.format(full_f, last_offset))

            with open(full_f) as fd:
                if last_offset > 0:
                    fd.seek(last_offset)
                    last_offset = -1
                cols = None
                while True:
                    l = fd.readline()
                    offset = fd.tell()
                    if not l:
                        break
                    l = l.rstrip()
                    if l[0] == '#':
                        continue
                    values = l.split()
                    s = values[0] + ' ' + values[1]
                    ts = pytz.utc.localize(datetime.datetime.strptime(s, '%Y-%m-%d %H:%M:%S')).astimezone(self.local_tz)
                    data = dict(zip(self._cols, values[2:]))
                    for ip_col in ['c_ip', 's_ip']:
                        r = self.geoip.lookup(data[ip_col])
                        if r:
                            data.update({ip_col + '_' + k: v for k, v in r.items()})

                    # parse ua
                    uas = data.get('cs_user_agent')
                    if uas:
                        data.update({'ua_' + k: v for k,v in self.get_ua(uas).items()})

                    data.update({'ts': ts,
                                 'offset': offset,
                                 'file': entry['file'],
                                 '_id': entry['file'] + ':' + str(offset),
                                 '_index': "{}-{:%Y.%m}".format(self.INDEX_PREFIX, ts),
                                 '_type': self.INDEX_PREFIX,
                                 })
                    self.total_count += 1
                    yield data

    def setup_template(self):
        #recs = [r for i, r in zip(range(128), self.getrec())]
        recs = itertools.islice(self.getrec(), 128)
        props = self.esu.get_props_from_recs(recs)
        for ip_col in ['c_ip', 's_ip']:
            props[ip_col] = {'type': 'ip'}
        for geo_col in ['c_ip_loc', 's_ip_loc']:
            props[geo_col] = {'type': 'geo_point'}
        template_name = self.INDEX_PREFIX + '_template'
        index_pattern = self.INDEX_PREFIX + '-*'
        self.esu.create_template(template_name, index_pattern, props)

    def run(self):
        body = {
            "_source": ["file", "offset"],
            "query": {"match_all": {}},
            "sort": [
                {"file": {"order": "desc"}},
                {"offset": {"order": "desc"}}
            ]
        }

        recs = lnr.esu.recs_search(index=lnr.INDEX_PREFIX + '-*', doc_type=lnr.INDEX_PREFIX, body=body, size=1,
                                   include_id=False)
        if len(recs) == 0:
            last_file, last_offset = None, -1
        else:
            last_file, last_offset = recs[0]['file'], recs[0]['offset']
        logging.info("Processing from Last File: {} Last Offset: {}".format(last_file, last_offset))
        summary = elasticsearch.helpers.bulk(self.esu._es, self.getrec(last_file, last_offset))
        logging.info("Loaded {} records".format(self.total_count))
        self.esu._es.indices.refresh(self.INDEX_PREFIX + '-*')
        logging.info("Summary: {}".format(summary))


# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':
    LOG_DIR =  r'N:\TEMP\LOGS'
    rv.misc.set_logging(LOG_DIR)

    lnr = IISLogReader()
    if len(sys.argv) > 1:
        assert sys.argv[1] == 'initialize_template', "Usage: {} [initialialize_template]".format(os.path.basename(sys.argv[0]))
        lnr.setup_template()
        sys.exit(0)

    lnr.run()
