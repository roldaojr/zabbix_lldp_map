#!/usr/bin/python3
import re
import os
import yaml
import pydot
from copy import copy
from locale import getpreferredencoding
from collections import defaultdict
from string import Template
from pyzabbix import ZabbixAPI
from pydot import Dot, Graph, Node, Edge
import urllib3

urllib3.disable_warnings()
config = {}

def get_config(name, default=None):
    keys = name.split('.')
    props = config
    for key in keys[:-1]:
        props = props.get(key, {})
    return props.get(keys[-1], default)

def get_images_paths(graph):
    images = set([node.get('image') for node in graph.get_nodes()])
    images.add(get_config('graphviz.attributes.node.image', ''))
    imagepath = os.path.abspath(get_config('graphviz.imagepath'))
    return [os.path.join(imagepath, img) for img in set(images) if img]


class CustomObject(object):
    def __init__(self, *args, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __repr__(self):
        props = ['%s=%s' % (k, v) for k, v in self.__dict__.items()]
        return '<%s %s>' % (type(self).__name__, ' '.join(props))


class ZabbixConnector(object):
    def __init__(self, url, username, password):
        print('Connecting to zabbix')
        self.api = ZabbixAPI(url)
        self.api.session.verify = False
        self.api.login(username, password)

    def get_hosts_from_group(self, hostgroup):
        print('Getting zabbix hosts from group %s' % hostgroup)
        groups = self.api.hostgroup.get(
            search={'name': hostgroup}, output=['groupid'])
        kwargs = {
            'groupids': groups[0]['groupid'],
            'output': ['hostid', 'interfaces', 'status', 'name'],
            'selectInventory': get_config(
                'zabbix.inventory_fields', []) + ['name', 'type'],
            'selectInterfaces': ['type', 'ip'],
            'filter': {'status': 0}
        }
        return self.api.host.get(**kwargs)

    def get_items(self, hosts, key, any=True):
        print('Getting data from hosts')
        kwargs = {
            'output': ['hostid', 'name', 'key_', 'lastvalue'],
            'hostids': [h['hostid'] for h in hosts],
            'search': {'key_': key},
            'searchByAny': any
        }
        return self.api.item.get(**kwargs)

    def get_icons(self):
        print('Getting zabbix icons')
        icons = {}
        iconsData = self.api.image.get(output=["imageid", "name"])
        for icon in iconsData:
            icons[icon["name"]] = icon["imageid"]
        return icons

def get_devices_from_zabbix(self, hostgroup):
    devices = {}
    hosts = self.get_hosts_from_group(hostgroup)
    for host in hosts:
        devices[host['hostid']] = CustomObject(
            name=host['name'],
            inventory=host['inventory'],
            neighbors=defaultdict(dict),
            sysname=host['inventory'].get('name', '')
        )

    items = self.get_items(hosts, 'lldp.loc.sys.name')
    for item in items:
        if item['lastvalue']:
            devices[item['hostid']].sysname = item['lastvalue']

    neighbors = self.get_items(hosts, ['lldp.loc.if.name',
                                       'lldp.loc.if.ifSpeed',
                                       'lldp.rem.sysname',
                                       'lldp.rem.port.id',
                                       'lldp.rem.port.type',
                                       'lldp.rem.port.desc'])
    for neighbor in neighbors:
        port = re.findall(r'\[Port - ([\w:\/]+)\]', neighbor['name'])
        prop = re.findall(r'^([\w\.]+)\[', neighbor['key_'])
        if neighbor['lastvalue'] != '** No Information **' and port and prop:
            devices[neighbor['hostid']].neighbors[port[0]][prop[0]] = neighbor['lastvalue']

    return devices


def generate_graph(devices):
    graph = Dot(graph_type='graph', strict=True)
    graph.set_graph_defaults(**get_config('graphviz.attributes.graph', {}))
    graph.set_node_defaults(**get_config('graphviz.attributes.node', {}))
    graph.set_edge_defaults(**get_config('graphviz.attributes.edge', {}))
    graph.zabbix_data = {}
    device_names = [d.sysname for d in devices.values() if getattr(d, 'sysname', None)]
    for i, (zabbix_id, device) in enumerate(devices.items(), start=1):
        if not getattr(device, 'sysname', None):
            continue
        device.inventory.update({'zabbix_name': device.name})
        label = Template(get_config(
            'graphviz.node_label_template', '$zabbix_name')).substitute(device.inventory)
        graph.add_node(Node(
            device.sysname, label=label,
            image=get_config('iconmap', {}).get(device.inventory.get('type', ''))))
        graph.zabbix_data[device.sysname] = {'zabbix_id': zabbix_id, 'index': i}
        for idx, neighbor in device.neighbors.items():
            remSysName = neighbor.get('lldp.rem.sysname')
            if remSysName is None or remSysName not in device_names:
                continue
            link_speed = int(neighbor.get('lldp.loc.if.ifSpeed', 0))//1000000
            link_attrs = get_config('graphviz.linkspeed', {})
            attrs = link_attrs.get(link_speed, {})
            if get_config('graphviz.edge_label'):
                if neighbor.get('lldp.rem.port.type') == '3':
                    key = 'lldp.rem.port.desc'
                else:
                    key = 'lldp.rem.port.id'
                attrs.update({
                    'taillabel': neighbor.get('lldp.loc.if.name', ''),
                    'headlabel': neighbor.get(key, '')
                })
            graph.add_edge(Edge(device.sysname, neighbor['lldp.rem.sysname'], **attrs))

    return graph


def generate_zabbix_map(zabbix, graph, mapname, width, height):
    print('Generating map %s (%dx%d)...' % (mapname, width, height))
    # Get zabbix icons ids
    icons = zabbix.get_icons()
    # Update graph attributes
    graph_defaults = graph.get_node('graph')[0].obj_dict['attributes']
    node_defaults = graph.get_node('node')[0].obj_dict['attributes']
    edge_defaults = graph.get_node('edge')[0].obj_dict['attributes']
    graph_defaults.update({
        'size': '%s,%s!' % (width/100, height/100),
        'dpi': 100,
        'ratio': 'fill'
    })
    node_defaults.update({'fixedsize': True, 'width': 0.5, 'height': 0.5})
    # Get nodes positions
    D_bytes = graph.create_dot(prog=get_config('graphviz.attributes.graph.layout', 'dot'))
    D = str(D_bytes, encoding=getpreferredencoding())
    # List of one or more "pydot.Dot" instances deserialized from this string.
    Q_list = pydot.graph_from_dot_data(D)
    assert len(Q_list) == 1
    # The first and only such instance, as guaranteed by the above assertion.
    graph_with_pos = Q_list[0]
    # Get map nodes
    icon_id = icons.get(get_config('zabbix.map.default_icon'), 'Switch_(24)')
    elements = []
    for node in graph_with_pos.get_node_list():
        if not node.get_pos(): continue
        data = graph.zabbix_data[node.get_name().strip('"')]
        x, y = str(node.get_pos()).strip('"').split(',')
        elements.append({
            'selementid': data['index'],
            'elements': [{'hostid': data['zabbix_id']}],
            'x': int(float(x)),
            'y': int(float(y)),
            'use_iconmap': 1,
            'elementtype': 0,
            'iconid_off': icon_id,
        })
    
    # Get map links
    links = []
    for edge in graph_with_pos.get_edge_list():
        edge_color = edge.get('color') or edge_defaults.get('color', '#000000')
        links.append({
            'selementid1': graph.zabbix_data[edge.get_source().strip('"')]['index'],
            'selementid2': graph.zabbix_data[edge.get_destination().strip('"')]['index'],
            'color': edge_color.lstrip('#')
        })

    map_params = {
        'name': mapname,
        'label_format': 1, # ADVANCED_LABELS
        'label_type_image': 0, #LABEL_TYPE_LABEL
        'width': width,
        'height': height,
        'selements': elements,
        'links': links
    }

    zabbix_map = zabbix.api.map.get(filter={'name': mapname})
    if zabbix_map:
        mapid = zabbix_map[0]['sysmapid']
        print('Updaing map %s (%s)...' % (mapname, mapid))
        zabbix.api.map.update({'sysmapid': mapid, 'links':[], 'selements':[], 'urls':[] })
        map_params["sysmapid"] = mapid
        zabbix.api.map.update(map_params)
    else:
        print('Creating new map %s...' % mapname)
        zabbix.api.map.create(map_params)


if __name__ == '__main__':
    with open('config.yml', 'r') as ymlfile:
        config = yaml.safe_load(ymlfile)
    zabbix = ZabbixConnector(get_config('zabbix.url'), get_config('zabbix.username'),
                             get_config('zabbix.password'))
    devices = get_devices_from_zabbix(zabbix, get_config('zabbix.hostgroup'))
    graph = generate_graph(devices)
    if get_config('graphviz.file'):
        graph.write(get_config('graphviz.file'))
    if get_config('graphviz.imagefile'):
        graph.set_shape_files(get_images_paths(graph))
        graph.write_png(get_config('graphviz.imagefile'))
    map_cfg = get_config('zabbix.map', {})
    if 'name' in map_cfg and map_cfg['name']:
        generate_zabbix_map(zabbix, graph, map_cfg['name'], map_cfg['width'], map_cfg['height'])
