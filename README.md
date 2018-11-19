# zabbix-lldp-map

## Requisitos
	* Python >=3.5
	* GraphViz (linha de comando)

## Usando

Executar o comando dentro na pasta do arquivo

	pip install -r requirements.txt

Para gerar o mapa

	python3 zabbix_lldp_map.py

## Configurações

As configurações podem ser definidas como variáveis e ambiente ou definidas dentro do arquivo .py

	* ZABBIX_URL (URL do servidor zabbix)
	* ZABBIX_USERNAME (usuário do zabbix)
	* ZABBIX_PASSWORD (senha do usuário do zabbix)
	* ZABBIX_HOSTGROUP (grupo de hosts a mostrar no mapa)
	* SNMP_COMMUNITY (comunidade snmp)
	* ZABBIX_MAP_NAME (nome do mapa no zabbix)
	* ZABBIX_MAP_WIDTH (largura do mapa em pixeis)
	* ZABBIX_MAP_HEIGHT (altura do mapa em pixeis)
