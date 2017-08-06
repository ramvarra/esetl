import os, re, sys
import radiotherm
import datetime
import json
import tzlocal
import logging
import requests
import rv.misc

import rv.ESUtil

# ----------------------------------------------------------------------------------------------------------------------
class TStatLoader:
    ES_CONFIG = dict(hosts='http://192.168.1.141:9200', timeout=240)
    INDEX_PREFIX = 'tstat'
    TSTATS = {
        'UP': '192.168.1.132',
        'DOWN': '192.168.1.133',
    }

    # ------------------------------------------------------------------------------------------------------------------
    def __init__(self):
        self.local_tz = tzlocal.get_localzone()
        self.esu = rv.ESUtil.ESUtil(**self.ES_CONFIG)
        self.index_time_pattern = self.INDEX_PREFIX + '-%Y.%w'
        self.index_pattern = self.INDEX_PREFIX + '-*'
        self.total_count = 0

    # ------------------------------------------------------------------------------------------------------------------
    def get_weather(self):
        if 'OWM_APPID' not in os.environ:
            raise Exception("OWM_APPID API key for OpenWeatherMap missing in ENV")

        url = 'http://api.openweathermap.org/data/2.5/weather'

        chandler = {'lat': 33.30, 'lon': -111.93}
        full_url = "{}?lat={}&lon={}&APPID={}".format(url, chandler['lat'], chandler['lon'],
                                                      os.environ['OWM_APPID'])

        r = requests.get(full_url)

        if r.status_code != 200:
            raise Exception("OpenWeatherMap url {} failed with Code: {} Reason: {}".format(url,
                                                                                           r.status_code, r.reason))
        d = json.loads(r.content)

        rec = {}
        rec['temp'] = round((d['main']['temp'] * 9.0 / 5.0) - 459.67, 2)  # kelvin to F
        rec['humidity'] = d['main']['humidity']
        rec['pressure'] = d['main']['pressure']
        rec['visibility'] = d['visibility']
        if 'clouds' in d:
            rec['clouds'] = d['clouds']['all']
        if 'wind' in d:
            rec['wind_speed'] = d['wind']['speed']
            rec['wind_dir'] = d['wind']['deg']
        if 'rain' in d:
            rec['rain_3h'] = d['rain']['3h']
        rec['sunrise'] = datetime.datetime.fromtimestamp(d['sys']['sunrise'])
        rec['sunset'] = datetime.datetime.fromtimestamp(d['sys']['sunset'])
        rec['description'] = ",".join(w['description'] for w in d['weather'])
        return rec

    # ------------------------------------------------------------------------------------------------------------------
    def get_tstat_recs(self):
        w = self.get_weather()
        recs = []
        for name, ip in self.TSTATS.items():
            th = radiotherm.get_thermostat(ip)
            d = {'name': name, 'ip': ip, 'ts': self.local_tz.localize(datetime.datetime.now())}
            d.update({k: v for k, v in th.tstat['raw'].items() if k != 'time'})
            dl = th.datalog['raw']
            for day in dl:
                for rt in dl[day]:
                    d["{}_{}".format(day, rt)] = dl[day][rt]['hour'] * 60 + dl[day][rt]['minute']
            d.update(w)
            recs.append(d)
        return recs

    # ------------------------------------------------------------------------------------------------------------------
    def initialize_template(self):
        recs = self.get_tstat_recs()
        template_name = self.INDEX_PREFIX + '_template'
        m = self.esu.get_mappings_from_recs(recs)
        self.esu.create_template(template_name, self.index_pattern, m)

    # ------------------------------------------------------------------------------------------------------------------
    def run(self):
        recs = self.get_tstat_recs()
        logging.info("Loading TSTAT Recs")
        n = self.esu.import_recs(recs, index=self.index_time_pattern, doc_type=self.INDEX_PREFIX, ts_field='ts')
        logging.info("Loaded {} records".format(n))
        self.esu._es.indices.refresh(self.index_pattern)


# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':
    LOG_DIR = '/var/log/esetl'
    rv.misc.set_logging(LOG_DIR)

    ldr = TStatLoader()
    if len(sys.argv) > 1:
        assert sys.argv[1] == 'initialize_template', "Usage: {} [initialialize_template]".format(os.path.basename(sys.argv[0]))
        ldr.initialize_template()
        sys.exit(0)

    try:
        ldr.run()
    except Exception as ex:
        logging.exception("Failed run with excpetion")

