import sys
import bgapi
import getopt
import time

OTA_SERVICE_UUID = 0x1d14d6eefd634fa1bfa48f47b42119f0
OTA_CONTROL_UUID = 0xf7bf3564fb6d4e5388a45e37e0326063
ignored_events = ['bt_evt_connection_parameters',
                  'bt_evt_connection_phy_status',
                  'bt_evt_connection_remote_used_features']

xapi = 'sl_bt.xapi'
connector = None
baudrate = 115200
target = {'address':None}
state = 'start'
duration = 10
timeout = None
app_rssi = None
verbose = 0
ota_mode = False
match_service = 0x1509
match_name = None
list_mode = False
devices = {}

def exit_help(error=None) :
    if None != error :
        print('Error: %s'%(error))
        print('Usage %s [ -h ][ -v ][ -t <ip-address> ][ -u <uart> ][ -b baudrate ]')
        print('         [ -x <api-xml> ][ -d <duration> ][ -n <complete-local-name> ]')
        print('         [ --ota ][ -a <bd-addr> ][ -l ]')
        quit()
        
opts,params = getopt.getopt(sys.argv[1:],'hvlt:u:x:b:a:n:d:',['ota'])
for opt,param in opts :
    if '-h' == opt :
        exit_help()
    if '-v' == opt :
        verbose += 1
    elif '-t' == opt :
        connector = bgapi.SocketConnector((param,4901))
    elif '-u' == opt :
        connector = bgapi.SerialConnector(param,bauderate=baudrate)
    elif '-x' == opt :
        xapi = param
    elif '-b' == opt :
        baudrate = int(param)
    elif '-l' == opt :
        list_mode = True
    elif '-d' == opt :
        duration = float(param)
    elif '-n' == opt :
        match_name = param
        match_service = None
        match_address = None
    elif '-a' == opt :
        match_address = param
        match_service = None
        match_name = None
    elif '--ota' == opt :
        ota_mode = True
    else :
        exit_help('Unrecognized option "%s"'%(opt))

if None == connector :
    exit_help('Either -t or -u is required')

try :
    dev = bgapi.BGLib(connection=connector,apis=xapi)
except FileNotFoundError :
    exit_help('xml file defining API, %s, not found. See -x option')

def setState(new_state) :
    global state
    print('set_state: %s -> %s'%(state,new_state))
    state = new_state

def process_adData(adData) :
    rc = {}
    while len(adData) :
        length = adData[0]
        type = adData[1]
        if length > len(adData[1:]) :
            return {}
        payload = adData[2:2+length-1]
        adData = adData[1+length:]
        if 1 == type :
            rc['Flags'] = payload[0]
        elif 2 == (type & 0xfe) :
            label = ['Inc','C'][type & 1]+'ompleteListOf16bitServices'
            services = {}
            while len(payload) :
                uuid = int.from_bytes(payload[0:2],'little')
                payload = payload[2:]
                services[uuid] = True
                rc[label] = services
        elif 9 == type :
            rc['CompleteLocalName'] = payload.decode()
    return rc

def updateTargetRssi(rssi,channel) :
    global target
    if None == channel :
        target['rssi'].append(rssi)
    else :
        target['rssi'][channel].append(rssi)

def clearTargetRssi() :
    global target
    rssi = target.get('rssi')
    if None == rssi : return setState('confused')
    if dict == type(rssi) :
        target['rssi'] = {37:[],38:[],39:[]}
    elif list == type(rssi) :
        target['rssi'] = []
    else :
        setState('confused')
        
def setTarget(address,address_type,rssi,channel) :
    global target
    global timeout
    print('Target address: %s'%(address))
    target['address'] = address
    target['address_type'] = address_type
    if None == channel :
        target['rssi'] = []
    else :
        target['rssi'] = {37:[],38:[],39:[]}
    updateTargetRssi(rssi,channel)
    timeout = time.time() + duration
    setState('watching-app')

def connectTarget() :
    dev.bt.connection.open(target['address'],target['address_type'],1)
    setState('connecting')

def rssi_stats(obj) :
    sum = 0
    count = 0
    if dict == type(obj) :
        for ch in obj :
            for rssi in obj[ch] :
                sum += rssi
                count +=1
    elif list == type(obj) :
        for rssi in obj :
            sum += rssi
            count += 1
    else :
        raise RuntimeError('confusion is not enough')
    return count,sum

def process_rssi() :
    if ota_mode :
        app_count,app_sum = rssi_stats(app_rssi)
        ota_count,ota_sum = rssi_stats(target['rssi'])
        app_mean = app_sum/app_count
        ota_mean = ota_sum/ota_count
        print('%d second duration: application %d packets, RSSI average %.1f, AppLoader %d packets, RSSI average %.1f (delta: %.1f dB)'%(duration,app_count,app_mean,ota_count,ota_mean,ota_mean-app_mean))
    else :
        count,sum = rssi_stats(target['rssi'])
        print('%d second  duration: %d packets, RSSI average %.1f, '%(duration,count,sum/count))
    setState('done')

def list_devices() :
    for addr in devices :
        str = addr
        name = devices[addr].get('CompleteLocalName')
        services = devices[addr].get('services')
        if None != name : str += ' Complete Local Name: %s'%(name)
        print(str)
        
def process_advertisement(addr,addrType,rssi,adData,channel=None) :
    if 'observing' == state :
        if None == devices.get(addr) :
            data = process_adData(adData)
            devices[addr] = data
        if time.time() > timeout :
            dev.bt.scanner.stop()
            list_devices()
            setState('done')
    elif 'searching' == state :
        data = process_adData(adData)
        if None != match_address :
            if addr == match_address :
                setTarget(addr,addrType,rssi,channel)
                return True                
        if None != match_service :
            services = data.get('CompleteListOf16bitServices')
            if None != services :
                if None != services.get(match_service) :
                    setTarget(addr,addrType,rssi,channel)
                    return True
        if None != match_name :
            name = data.get('CompleteLocalName')
            if match_name == name :
                setTarget(addr,addrType,rssi,channel)
                return True
        if time.time() > timeout :
            dev.bt.scanner.stop()
            setState('done')
        return False
    if addr != target['address'] : return False
    if 'watching-' == state[:-3] :
        updateTargetRssi(rssi,channel)
        if time.time() > timeout :
            dev.bt.scanner.stop()
            if ota_mode and 'watching-app' == state :
                connectTarget()
            else :
                process_rssi()
    else :
        return False
    return True


def setTargetService(handle,uuid) :
    global target
    services = target.get('services')
    if None == services :
        services = {}
    services[uuid] = {'handle':handle,'characteristics':{}}
    target['services'] = services
    #print('uuid: 0x%x'%(uuid))
    
def discover_ota(connection) :
    global target
    services = target.get('services')
    if None == services : return setState('confused')
    target['current-service-uuid'] = OTA_SERVICE_UUID
    ota_service = services.get(target['current-service-uuid'])
    if None == ota_service : return setState('confused')
    handle = ota_service.get('handle')
    if None == handle : return setService('confused')
    dev.bt.gatt.discover_characteristics(connection,handle)
    setState('discovering-ota-characteristics')

def setTargetCharacteristic(handle,uuid) :
    services = target.get('services')
    if None == services : return setState('confused')
    currentService = target.get('current-service-uuid')
    if None == currentService : return setState('confused')
    ota_service = services.get(currentService)
    if None == ota_service : return setState('confused')
    characteristics = ota_service.get('characteristics')
    if None == characteristics : return setState('confused')
    characteristics[uuid] = {'handle':handle,'descriptors':{}}
    #print('uuid: 0x%x'%(uuid))
    
def initiate_ota(connection) :
    global target
    services = target.get('services')
    if None == services : return setState('confused')
    ota_service = services.get(target['current-service-uuid'])
    if None == ota_service : return setState('confused')
    characteristics = ota_service.get('characteristics')
    if None == characteristics : return setState('confused')
    ota_control = characteristics.get(OTA_CONTROL_UUID)
    if None == ota_control : return setState('confused')
    handle = ota_control.get('handle')
    if None == handle : return setState('confused')
    dev.bt.gatt.write_characteristic_value(connection,handle,b'\x00')
    setState('writing-ota-control')
    
def sl_bt_on_event(evt) :
    global app_rssi
    global timeout
    if 'bt_evt_system_boot' == evt :
        print('system-boot: BLE SDK %dv%dp%db%d'%(evt.major,evt.minor,evt.patch,evt.build))
        dev.bt.scanner.start(1,2)
        if list_mode :
            setState('observing')
        else :
            setState('searching')
        timeout = time.time() + duration
    elif 'bt_evt_scanner_legacy_advertisement_report' == evt :
        rc = process_advertisement(evt.address,evt.address_type,evt.rssi,evt.data,evt.channel)
        if verbose and (rc or (verbose > 1)) : print(evt)
    elif 'bt_evt_scanner_scan_report' == evt :
        process_advertisement(evt.address,evt.address_type,evt.rssi,evt.data)
    elif 'bt_evt_connection_opened' == evt :
        if 'connecting' != state :
            setState('confused')
        setState('connected')
    elif 'bt_evt_gatt_mtu_exchanged' == evt :
        setState('discovering-services')
        dev.bt.gatt.discover_primary_services(evt.connection)
    elif 'bt_evt_gatt_service' == evt :
        setTargetService(evt.service,int.from_bytes(evt.uuid,'little'))
    elif 'bt_evt_gatt_characteristic' == evt :
        setTargetCharacteristic(evt.characteristic,int.from_bytes(evt.uuid,'little'))
    elif 'bt_evt_gatt_procedure_completed' == evt:
        if 'discovering-services' == state :
            discover_ota(evt.connection)
        elif 'discovering-ota-characteristics' == state :
            initiate_ota(evt.connection)
        elif 'writing-ota-control' == state :
            setState('expecting-close')
        else :
            setState('confused')
    elif 'bt_evt_connection_closed' == evt :
        if 'expecting-close' :
            app_rssi = target.get('rssi')
            clearTargetRssi()
            dev.bt.scanner.start(1,2)
            setState('watching-ota')
            timeout = time.time() + duration
    else :
        unhandled = True
        for ignore in ignored_events :
            if ignore == evt :
                unhandled = False
        if unhandled :
            print('Unhandled event: %s'%(evt.__str__()))
    return state != 'confused'

dev.open()
dev.bt.system.reset(0)
setState('reset')

# keep scanning for events
while 'done' != state :
    try:
        # print('Starting point...')
        evt = dev.get_events(max_events=1)
        if evt:
            if not sl_bt_on_event(evt[0]) :
                break
    except(KeyboardInterrupt, SystemExit) as e:
        if dev.is_open():
            dev.close()
            print('Exiting...')
            sys.exit(1)

if dev.is_open():
    dev.close()

