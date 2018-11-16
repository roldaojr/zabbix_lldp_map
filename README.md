# zabbix-lldp-map

## Requisitos
	* Python >=3.5
	* Pipenv >=2018
	* GraphViz

## Usando

Executar o comando dentro na pasta do arquivo

	pipenv install

Para gerar o mapa

	pipenv run zabbix_lldp_map

## Configurações

As configurações podem ser definidas como variáveis e ambiente ou definidas dentro de um arquivo .env

	* ZABBIX_URL (URL do servidor zabbix)
	* ZABBIX_USERNAME (usuário do zabbix)
	* ZABBIX_PASSWORD (senha do usuário do zabbix)
	* ZABBIX_HOSTGROUP (grupo de hosts a usar no mapa)
	* SNMP_COMMUNITY (comunidade snmp)
	* ZABBIX_MAP_NAME (nome do mapa no zabbix)
	* ZABBIX_MAP_WIDTH (largura do mapa)
	* ZABBIX_MAP_HEIGHT (altura do mapa)
