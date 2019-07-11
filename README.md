# zabbix-lldp-map

Gerar mapa da rede a partir dos dados de LLDP

## Requisitos
    * Python 3
    * pydot
    * PyYAML
    * pyzabbix

Para que o mapa seja gerado é necessário a instalação do modulo do zabbix para descoberta LLDP (https://github.com/zabbix-book/snmp_lldp). E a importação dos templates incluídos do projeto.


## Instalando

Executar o comando dentro na pasta do projeto

    pip install -r requirements.txt

## Gerar o mapa

Criar um grupo de hosts e adicionar os dispositivos que serão exibidos no mapa

Copiar config-example.yml para config.yml e fazer as alterações necessárias

Executar o seguinte comando na pasta do projeto

    python3 zabbix_lldp_map.py
