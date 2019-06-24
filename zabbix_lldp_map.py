#!/usr/bin/python3
import re
import os
import yaml
import pydot
from collections import defaultdict
from itertools import groupby, chain
from string import Template
from pyzabbix import ZabbixAPI
import networkx as nx
import urllib3
urllib3.disable_warnings()

with open('config.yml', 'r') as ymlfile:
    config = yaml.safe_load(ymlfile)

def get_config(name, default=None):
    keys = name.split('.')
    props = config
    for key in keys[:-1]:
        props = props.get(key, {})
    return props.get(keys[-1], default)


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

    def get_devices(self, hostgroup):
        devices = {}
        hosts = self.get_hosts_from_group(hostgroup)
        for host in hosts:
            devices[host['hostid']] = NetworkDevice(
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


class NetworkDevice(object):
    def __init__(self, *args, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __repr__(self):
        props = ['%s=%s' % (k, v) for k, v in self.__dict__.items()]
        return '<%s %s>' % (type(self).__name__, ' '.join(props))


class LLdpGraphGenerator(object):
    def __init__(self, devices):
        self._devices = devices

    def _get_port_number(self, text):
        result = re.findall(r'(\d+)$', text)
        if len(result) > 0:
            return result[0]
        else:
            return ''

    def _get_attributes(self, section, key):
        attributes = config.get('graphviz', {}).get(section, {})
        return attributes.get(key, {})

    def get_pydot(self, graph):
        # remove zabbix map attributes
        for name, data in graph.nodes(data=True):
            if 'zabbix_id' in data:
                del data['zabbix_id']
            if 'index' in data:
                del data['index']
        # create pyDot Graph
        currentG = nx.drawing.nx_pydot.to_pydot(graph)
        dotG = pydot.Dot(graph_type='graph', strict=True)
        # collect image names
        images = set(nx.get_node_attributes(graph, 'image').values())
        defaultImage = get_config('graphviz.attributes.node.image')
        if defaultImage:
            images.add(defaultImage)
        imagepath = os.path.abspath(get_config('graphviz.imagepath'))
        dotG.set_shape_files([os.path.join(imagepath, img) for img in set(images) if img is not None])
        # set graph, edge and node defaults
        dotG.set_graph_defaults(**get_config('graphviz.attributes.graph', {}))
        dotG.set_node_defaults(**get_config('graphviz.attributes.node', {}))
        dotG.set_edge_defaults(**get_config('graphviz.attributes.edge', {}))
        # add nodes and edges to pyDot graph
        for node in currentG.get_nodes():
            dotG.add_node(node)
        for edge in currentG.get_edges():
            dotG.add_edge(edge)
        # return pyDot graph
        return dotG

    def save_graphviz_file(self, graph, filename):
        pydotG = self.get_pydot(graph)
        pydotG.write(filename)

    def save_image(self, graph, filename):
        pydotG = self.get_pydot(graph)
        pydotG.write_png(filename)

    def get_graph(self):
        graph = nx.Graph()
        graph.attrs = self._get_attributes('attributes', 'graph')
        graph.node_attrs = self._get_attributes('attributes', 'node')
        graph.edge_attrs = self._get_attributes('attributes', 'edge')
        device_names = [d.sysname for d in self._devices.values() if getattr(d, 'sysname', None)]
        for i, (zabbix_id, device) in enumerate(self._devices.items(), start=1):
            if not getattr(device, 'sysname', None):
                continue
            device.inventory.update({'zabbix_name': device.name})
            label = Template(get_config(
                'graphviz.node_label_template', '$zabbix_name')).substitute(device.inventory)
            graph.add_node(device.sysname, zabbix_id=zabbix_id, index=i, label=label,
                           image=get_config('iconmap', {}).get(device.inventory.get('type', '')))
            for idx, neighbor in device.neighbors.items():
                remSysName = neighbor.get('lldp.rem.sysname')
                if remSysName is None or remSysName not in device_names:
                    continue
                linkSpeed = int(neighbor.get('lldp.loc.if.ifSpeed', 0))//1000000
                attrs = self._get_attributes('linkspeed', linkSpeed)
                if get_config('graphviz.edge_label'):
                    if neighbor.get('lldp.rem.port.type') == '3':
                        key = 'lldp.rem.port.desc'
                    else:
                        key = 'lldp.rem.port.id'
                    attrs.update({
                        'taillabel': neighbor.get('lldp.loc.if.name', ''),
                        'headlabel': neighbor.get(key, '')
                    })
                graph.add_edge(device.sysname, neighbor['lldp.rem.sysname'], **attrs)

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
            'use_iconmap': 1,
            'elementtype': 0,
            'iconid_off': self._icons['Switch_(24)'],
        }

    def _generate_map_elements(self, G, width, height):
        G.graph['dpi'] = 100
        G.graph['size'] = '%s,%s!' % (width/100, height/100)
        G.graph['ratio'] = 'fill'
        G.graph.update(get_config('graphviz.attributes.graph', {}))
        nodes_idx = nx.get_node_attributes(G, 'index')
        zabbix_ids = nx.get_node_attributes(G, 'zabbix_id')
        nodes_pos = nx.nx_pydot.graphviz_layout(
            G, get_config('graphviz.attributes.graph.layout', 'dot'))
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
        print('Generating map %s (%dx%d)...' % (mapname, width, height))
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
    zbx_config = get_config('zabbix')
    zabbix = ZabbixConnector(zbx_config['url'], zbx_config['username'],
                             zbx_config['password'])
    devices = zabbix.get_devices(zbx_config['hostgroup'])
    generator = LLdpGraphGenerator(devices)
    graph = generator.get_graph()
    map_cfg = zbx_config.get('map')
    if 'name' in map_cfg and map_cfg['name']:
        map_generator = GraphToZabbixMap(zabbix)
        map_generator.generate_zabbix_map(graph, map_cfg['name'], 
                                          map_cfg['width'], map_cfg['height'])
    if get_config('graphviz.file'):
        generator.save_graphviz_file(graph, get_config('graphviz.file'))
    if get_config('graphviz.imagefile'):
        generator.save_image(graph, get_config('graphviz.imagefile'))
