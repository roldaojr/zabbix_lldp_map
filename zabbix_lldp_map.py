#!/usr/bin/python3
import asyncio
import os
from pyzabbix import ZabbixAPI
from pysnmp.hlapi import *
import networkx as nx
import urllib3
urllib3.disable_warnings()

ZABBIX_URL = os.getenv('ZABBIX_URL', 'https://localhost/zabbix')
ZABBIX_USERNAME = os.getenv('ZABBIX_USERNAME', 'zabbix')
ZABBIX_PASSWORD = os.getenv('ZABBIX_PASSWORD', 'zabbix')
ZABBIX_HOSTGROUP = os.getenv('ZABBIX_HOSTGROUP', 'Switches')
SNMP_COMMUNITY = os.getenv('SNMP_COMMUNITY', 'públic')
ZABBIX_MAP_NAME = os.getenv('ZABBIX_MAP_NAME', 'LLDP Devices')
ZABBIX_MAP_WIDTH = int(os.getenv('ZABBIX_MAP_WIDTH', 1280))
ZABBIX_MAP_HEIGHT = int(os.getenv('ZABBIX_MAP_HEIGHT', 720))

class ZabbixConnector(object):
    def __init__(self, url, username, password):
        self.api = ZabbixAPI(url)
        self.api.session.verify = False
        self.api.login(username, password)

    def get_hosts_from_group(self, hostgroup):
        groups = self.api.hostgroup.get(
            search={'name': hostgroup}, output=['groupid'])
        kwargs = {
            'groupids': groups[0]['groupid'],
            'output': ['hostid', 'interfaces', 'status'],
            'selectInterfaces': ['type', 'ip'],
            'filter': {'status': 0}
        }
        return self.api.host.get(**kwargs)

    def get_icons(self):
        icons = {}
        iconsData = self.api.image.get(output=["imageid","name"])
        for icon in iconsData:
            icons[icon["name"]] = icon["imageid"]
        return icons

    def get_devices(self, hostgroup):
        devices = []
        for host in self.get_hosts_from_group(hostgroup):
            devices.append(LldpDevice(
                ipaddress=host['interfaces'][0]['ip'],
                zabbix_id=host['hostid']
            ))
        return devices


class Snmp(object):
    @classmethod
    def get(cls, ipaddress, community, oid):
        errorIndication, errorStatus, errorIndex, varBinds = next(
            getCmd(SnmpEngine(),
                   CommunityData(community), 
                   UdpTransportTarget((ipaddress, 161)),
                   ContextData(),
                   ObjectType(ObjectIdentity(oid)),
                   lexicographicMode=False,
                   lookupMib=False))

        if errorIndication:
            print(errorIndication)
            return
        elif errorStatus:
            print('%s at %s' % (
                errorStatus.prettyPrint(),
                errorIndex and varBinds[int(errorIndex) - 1][0] or '?'
            ))
        else:
            for varBind in varBinds:
                yield varBind[0].prettyPrint(), varBind[1].prettyPrint()

    @classmethod
    def walk(cls, ipaddress, community, oid):
        for (errorIndication,
            errorStatus,
            errorIndex,
            varBindTable) in nextCmd(SnmpEngine(),
                                     CommunityData(community), 
                                     UdpTransportTarget((ipaddress, 161)),
                                     ContextData(),
                                     ObjectType(ObjectIdentity(oid)),
                                     lexicographicMode=False,
                                     lookupMib=False):
            if errorIndication:
                print(errorIndication)
                break
            elif errorStatus:
                print('%s at %s' % (
                    errorStatus.prettyPrint(),
                    errorIndex and varBinds[int(errorIndex) - 1][0] or '?'
                ))
            else:
                for varBind in varBindTable:
                    yield varBind[0].prettyPrint(), varBind[1].prettyPrint()


class LldpDevice(object):
    def __init__(self, *args, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __repr__(self):
        props = ['%s=%s' % (k, v) for k, v in self.__dict__.items()]
        return '<%s %s>' % (type(self).__name__, ' '.join(props))


class LLdpGraphGenerator(object):
    def __init__(self, devices):
        self._devices = devices

    async def device_locChassisId(self, device):
        snmp_query = dict(Snmp.get(device.ipaddress, device.community, '1.0.8802.1.1.2.1.3.2.0'))
        device.locChassisId = next(iter(snmp_query.values()))
        return device

    async def device_remChassisIds(self, device, chassisId_list):
        snmp_query = dict(Snmp.walk(device.ipaddress, device.community, '1.0.8802.1.1.2.1.4.1.1.5'))
        device.remChassisIds = [cid for cid in snmp_query.values() if cid in chassisId_list]
        return device

    def _get_lldp_data(self):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(asyncio.gather(
            *[self.device_locChassisId(d) for d in self._devices]
        ))
        chassisId_list = [device.locChassisId for device in devices]
        loop.run_until_complete(asyncio.gather(
            *[self.device_remChassisIds(d, chassisId_list) for d in self._devices]
        ))

    def get_graph(self):
        self._get_lldp_data()
        graph = nx.Graph()
        for i, device in enumerate(self._devices, start=1):
            graph.add_node(device.locChassisId, zabbix_id=device.zabbix_id, index=i)
            for chassisId in device.remChassisIds:
                graph.add_edge(device.locChassisId, chassisId)
        return graph


class GraphToZabbixMap(object):
    def __init__(self, zabbix):
        self._zabbix = zabbix
        self._icons = zabbix.get_icons()

    def _get_map_element(self, element_id, zabbix_id, x, y):
        return {
            'selementid': element_id,
            'elements': [{'hostid': zabbix_id}],
            'x': x,
            'y': y,
            'use_iconmap': 0,
            'elementtype': 0,
            'iconid_off': self._icons['Switch_(48)'],
        }

    def _generate_map_elements(self, G, width, height):
        G.graph['dpi'] = 100
        G.graph['size'] = '%s,%s!' % (width/100, height/100)
        G.graph['ratio'] = 'fill'
        nodes_idx = nx.get_node_attributes(G, 'index')
        zabbix_ids = nx.get_node_attributes(G, 'zabbix_id')
        nodes_pos = nx.nx_pydot.graphviz_layout(G, 'twopi')
        max_x, max_y = map(max, zip(*nodes_pos.values()))
        elements = []

        for i, (node, (x, y)) in enumerate(nodes_pos.items(), start=1):
            node_x = int(x*width/max_x*0.9-x*0.1)
            node_y = int((height-y*height/max_y)*0.9+y*0.1)
            elements.append(self._get_map_element(nodes_idx[node], zabbix_ids[node], node_x, node_y))

        return elements

    def _generate_map_links(self, G):
        nodes_idx = nx.get_node_attributes(G, 'index')
        links = []
        for node1, node2, data in G.edges(data=True):
            links.append({
                'selementid1': nodes_idx[node1],
                'selementid2': nodes_idx[node2],
            })
        return links

    def generate_zabbix_map(self, G, mapname, width, height):
        map_params = {
            'name': mapname,
            'label_format': 1, # ADVANCED_LABELS
            'label_type_image': 0, #LABEL_TYPE_LABEL
            'width': width,
            'height': height,
            'selements': self._generate_map_elements(G, width, height),
            'links': self._generate_map_links(G)
        }
        zabbix_map = self._zabbix.api.map.get(filter={'name': mapname})
        if zabbix_map:
            mapid = zabbix_map[0]['sysmapid']
            print('Updaing map %s (%s)...' % (mapname, mapid))
            self._zabbix.api.map.update({'sysmapid': mapid, 'links':[], 'selements':[], 'urls':[] })
            map_params["sysmapid"] = mapid
            zbx_map = self._zabbix.api.map.update(map_params)
        else:
            print('Creating new map %s...' % mapname)
            zbx_map = self._zabbix.api.map.create(map_params)


if __name__ == '__main__':
    zabbix = ZabbixConnector(ZABBIX_URL, ZABBIX_USERNAME, ZABBIX_PASSWORD)
    devices = zabbix.get_devices(ZABBIX_HOSTGROUP)
    for device in devices:
        device.community = SNMP_COMMUNITY
    graph = LLdpGraphGenerator(devices).get_graph()
    map_generator = GraphToZabbixMap(zabbix)
    map_generator.generate_zabbix_map(graph, ZABBIX_MAP_NAME, ZABBIX_MAP_WIDTH, ZABBIX_MAP_HEIGHT)
