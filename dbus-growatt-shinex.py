#!/usr/bin/env python

# import normal packages
import platform
import logging
import logging.handlers
import sys
import os
import sys
import json
if sys.version_info.major == 2:
    import gobject
else:
    from gi.repository import GLib as gobject
import sys
import time
import requests # for http GET
import configparser # for config/ini file

# our own packages from victron
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService


class DbusGrowattShineXService:
  def __init__(self, servicename, paths, productname='Growatt ShineX', connection='Growatt ShineX HTTP Json Connection'):
    config = self._getConfig()
    deviceinstance = int(config['DEFAULT']['Deviceinstance'])
    customname = config['DEFAULT']['CustomName']

    self._dbusservice = VeDbusService("{}.http_{:02d}".format(servicename, deviceinstance))
    self._paths = paths

    logging.debug("%s /DeviceInstance = %d" % (servicename, deviceinstance))

    # Create the management objects, as specified in the ccgx dbus-api document
    self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
    self._dbusservice.add_path('/Mgmt/ProcessVersion', 'Unkown version, and running on Python ' + platform.python_version())
    self._dbusservice.add_path('/Mgmt/Connection', connection)

    # Create the mandatory objects
    self._dbusservice.add_path('/DeviceInstance', deviceinstance)
    self._dbusservice.add_path('/ProductId',0xA142) # id needs to be assigned by Victron Support current value for testing
    self._dbusservice.add_path('/ProductName', productname)
    self._dbusservice.add_path('/CustomName', customname)
    self._dbusservice.add_path('/Latency', None)
    self._dbusservice.add_path('/FirmwareVersion', 0.2)
    self._dbusservice.add_path('/HardwareVersion', 0)
    self._dbusservice.add_path('/Connected', 1)
    self._dbusservice.add_path('/Role', 'pvinverter')
    self._dbusservice.add_path('/Position', int(config['DEFAULT']['Position'])) # normaly only needed for pvinverter
    self._dbusservice.add_path('/Serial', self._getShineXSerial())
    self._dbusservice.add_path('/UpdateIndex', 0)
    self._dbusservice.add_path('/StatusCode', 7)

    # add path values to dbus
    for path, settings in self._paths.items():
      self._dbusservice.add_path(
        path, settings['initial'], gettextcallback=settings['textformat'], writeable=True, onchangecallback=self._handlechangedvalue)

    # last update
    self._lastUpdate = 0

    # add _update function 'timer'
    gobject.timeout_add(2000, self._update) # pause 1000ms before the next request

    # add _signOfLife 'timer' to get feedback in log every 5minutes
    gobject.timeout_add(self._getSignOfLifeInterval()*60*1000, self._signOfLife)

  def _getShineXSerial(self):
    meter_data = self._getShineXData()
    try:
      serial = meter_data['Mac']
    except:
      serial = '00:00:00:00:00:00'
    return serial.replace(':','')


  def _getConfig(self):
    config = configparser.ConfigParser()
    config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
    return config;


  def _getSignOfLifeInterval(self):
    config = self._getConfig()
    value = config['DEFAULT']['SignOfLifeLog']

    if not value:
        value = 0

    return int(value)


  def _getShineXStatusUrl(self):
    config = self._getConfig()
    accessType = config['DEFAULT']['AccessType']

    if accessType == 'OnPremise':
        URL = "http://%s:%s@%s/status" % (config['ONPREMISE']['Username'], config['ONPREMISE']['Password'], config['ONPREMISE']['Host'])
        URL = URL.replace(":@", "")
    else:
        raise ValueError("AccessType %s is not supported" % (config['DEFAULT']['AccessType']))

    return URL


  def _getShineXData(self):
    URL = self._getShineXStatusUrl()
    headers={}
    headers['Content-Type'] = 'application/json'

    meter_data = {"InverterStatus":0}

    try:
      meter_r = requests.get(url = URL, timeout=10,headers=headers)
      if ( meter_r.status_code == 200 and meter_r.headers.get('Content-Type').startswith('text/html')):
        REBOOT_URL = URL.replace('/status','/restart')
        resp = requests.get(url = REBOOT_URL, timeout = 10)
        logging.info("Reboot triggered")
    except requests.exceptions.Timeout:
      logging.info("RequestTimeout")
    except requests.exceptions.TooManyRedirects:
      print("Too Many Redirects")
    except requests.exceptions.RequestException as e:
      logging.info("No response from Shine X - %s" % (URL))
      print(e)
    except:
      logging.info("No response from Shine X - %s" % (URL))

    try:
      meter_data = meter_r.json()
    except:
        logging.info("Got no Json. meter_data set to: %s" % (meter_data))

    return meter_data


  def _signOfLife(self):
    logging.info("--- Start: sign of life ---")
    logging.info("Last _update() call: %s" % (self._lastUpdate))
    logging.info("Last '/Ac/Power': %s" % (self._dbusservice['/Ac/Power']))
    logging.info("Last '/Ac/Energy/Forward': %s" % (self._dbusservice['/Ac/Energy/Forward']))
    logging.info("--- End: sign of life ---")
    return True

  def _update(self):
    try:
      config = self._getConfig()
      phase = config['DEFAULT']['Phase']
      #get data from Shine X

      #send data to DBus
      meter_data = self._getShineXData()
      if meter_data is False:
        logging.info("Did not got valid Json.")
        return True

      self._dbusservice['/Connected'] = meter_data['InverterStatus']

      if meter_data['InverterStatus'] == 0:
        PhaseList = ['L1','L2','L3']
        for Phase in PhaseList:
          dbCur = '/Ac/{}/Current'.format(Phase)
          dbPow = '/Ac/{}/Power'.format(Phase)
          dbVol = '/Ac/{}/Voltage'.format(Phase)
          mCur = '{}ThreePhaseGridOutputCurrent'.format(Phase)
          mPow = '{}ThreePhaseGridOutputPower'.format(Phase)
          mVol = '{}ThreePhaseGridVoltage'.format(Phase)

          self._dbusservice[dbCur] = 0
          self._dbusservice[dbPow] = 0
          self._dbusservice[dbVol] = 0

        self._dbusservice['/Ac/Power'] = 0
        return True

      if meter_data['PV1InputPower'] == 0 and meter_data['PV1InputPower'] == 0:
        self._dbusservice['/Ac/Energy/Forward'] = meter_data['TotalGenerateEnergy']
        self._dbusservice['/Ac/Power'] = 0
        return True

      if meter_data['L2ThreePhaseGridOutputPower'] > 0:
        PhaseList = ['L1','L2','L3']
        for Phase in PhaseList:
          dbsname = '/Ac/{}/Energy/Forward'.format(Phase)
          self._dbusservice[dbsname] = ( meter_data['TotalGenerateEnergy'] / 3 )
      else:
        PhaseList = ['L1']
        self._dbusservice['/Ac/L1/Energy/Forward'] = meter_data['TotalGenerateEnergy']
        self._dbusservice['/Ac/L2/Energy/Forward'] = 0
        self._dbusservice['/Ac/L3/Energy/Forward'] = 0

      if meter_data['TotalGenerateEnergy'] > 0:
        self._dbusservice['/Ac/Energy/Forward'] = meter_data['TotalGenerateEnergy']
        self._dbusservice['/Ac/Power'] = meter_data['OutputPower']

        for Phase in PhaseList:
          dbCur = '/Ac/{}/Current'.format(Phase)
          dbPow = '/Ac/{}/Power'.format(Phase)
          dbVol = '/Ac/{}/Voltage'.format(Phase)
          mCur = '{}ThreePhaseGridOutputCurrent'.format(Phase)
          mPow = '{}ThreePhaseGridOutputPower'.format(Phase)
          mVol = '{}ThreePhaseGridVoltage'.format(Phase)

          if meter_data[mCur] == 0.5:
            meter_data[mCur] = (meter_data['OutputPower'] / 3)/meter_data[mVol]
          self._dbusservice[dbCur] = meter_data[mCur]
          self._dbusservice[dbPow] = meter_data[mCur] * meter_data[mVol]
          self._dbusservice[dbVol] = meter_data[mVol]

      #logging
      logging.debug("House Consumption (/Ac/Power): %s" % (self._dbusservice['/Ac/Power']))
      logging.debug("House Forward (/Ac/Energy/Forward): %s" % (self._dbusservice['/Ac/Energy/Forward']))
      logging.debug("---");

      self._dbusservice['/UpdateIndex'] = (self._dbusservice['/UpdateIndex'] + 1 ) % 256
      self._lastUpdate = time.time()
    except Exception as e:
      logging.critical('Error at %s', '_update', exc_info=e)

    # return true, otherwise add_timeout will be removed from GObject - see docs http://library.isr.ist.utl.pt/docs/pygtk2reference/gobject-functions.html#function-gobject--timeout-add
    return True

  def _handlechangedvalue(self, path, value):
    logging.debug("someone else updated %s to %s" % (path, value))
    return True # accept the change



def main():
  logpath = '/var/log/%s' % (os.path.dirname(os.path.realpath(__file__)).split('/')[-1])
  if not os.path.exists(logpath):
      os.mkdir(logpath,0o755)
  #configure logging
  log_rotate_handler = logging.handlers.RotatingFileHandler(
      maxBytes=5*1024*1024*10,
      backupCount=2,
      encoding=None,
      delay=0,
      filename="%s/current.log" % (logpath))
  logging.basicConfig(format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
      datefmt='%Y-%m-%d %H:%M:%S',
      level=logging.INFO,
      handlers=[
      logging.StreamHandler(),
      log_rotate_handler
  ])
  try:
      logging.info("Start");
      from dbus.mainloop.glib import DBusGMainLoop
      # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
      DBusGMainLoop(set_as_default=True)

      config = configparser.ConfigParser()
      config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))

      #formatting
      _kwh = lambda p, v: (str(round(v, 2)) + 'KWh')
      _a = lambda p, v: (str(round(v, 1)) + 'A')
      _w = lambda p, v: (str(round(v, 1)) + 'W')
      _v = lambda p, v: (str(round(v, 1)) + 'V')

      #start our main-service
      pvac_output = DbusGrowattShineXService(
        servicename='com.victronenergy.pvinverter',
        paths={
            '/ErrorCode': {'initial': None, 'textformat': '' },
            '/Ac/Energy/Forward': {'initial': None, 'textformat': _kwh},
            '/Ac/Power': {'initial': None, 'textformat': _w},

            '/Ac/L1/Current': {'initial': None, 'textformat': _a},
            '/Ac/L1/Power': {'initial': None, 'textformat': _w},
            '/Ac/L1/Voltage': {'initial': None, 'textformat': _v},
            '/Ac/L1/Energy/Forward': {'initial': None, 'textformat': _kwh},

            '/Ac/L2/Current': {'initial': None, 'textformat': _a},
            '/Ac/L2/Power': {'initial': None, 'textformat': _w},
            '/Ac/L2/Voltage': {'initial': None, 'textformat': _v},
            '/Ac/L2/Energy/Forward': {'initial': None, 'textformat': _kwh},

            '/Ac/L3/Current': {'initial': None, 'textformat': _a},
            '/Ac/L3/Power': {'initial': None, 'textformat': _w},
            '/Ac/L3/Voltage': {'initial': None, 'textformat': _v},
            '/Ac/L3/Energy/Forward': {'initial': None, 'textformat': _kwh},
        })

      logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
      mainloop = gobject.MainLoop()
      mainloop.run()
  except Exception as e:
    logging.critical('Error at %s', 'main', exc_info=e)


if __name__ == "__main__":
  main()
