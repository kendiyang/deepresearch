import requests
import yaml
from typing import List, Dict
import time

url = "https://console.vast.ai/api/v0/bundles/"

payload = {
    "limit": 1,
    "type": "on-demand",
    "verified": { "eq": True },
    "rentable": { "eq": True },
    "rented": { "eq": False },
    "order": [["dph_total", ""]],
    "reliability": { "gt": 0.9 }
}
headers = {
    "Authorization": "Bearer 1e654c94238c0275b5a75e25df176c36096a5fc5e55c23f98b13bdaab468f79b",
    "Content-Type": "application/json"
}

response = requests.post(url, json=payload, headers=headers)

host_id= response.json().get('offers')[0].get('id')

print("**************host_id:",host_id)

url = f"https://console.vast.ai/api/v0/asks/{host_id}/"

print("-----------url:",url)

hostname = f"node-{host_id}"

image = "kendiyang401/ansible-python3:latest"

payload = { 
    "image": image , 
    "template_hash_id": "13f70b9a0105604f2759dc0a18bf5d2f"
}

headers = {
    "Authorization": "Bearer 1e654c94238c0275b5a75e25df176c36096a5fc5e55c23f98b13bdaab468f79b",
    "Content-Type": "application/json"
}

response = requests.put(url, json=payload, headers=headers)

print("==========================instances:",response.json())

instance_id = response.json().get('new_contract')

url = f"https://console.vast.ai/api/v0/instances/{instance_id}/"

headers = {"Authorization": "Bearer 1e654c94238c0275b5a75e25df176c36096a5fc5e55c23f98b13bdaab468f79b"}

host_ip = ''
host_port = 0

ok = True
while ok :
    response = requests.get(url, headers=headers)

    info = response.json()

    host_ip = info.get('instances').get('public_ipaddr')
    ports = info.get('instances').get('ports')

    if ports is None :
        time.sleep(1)
        continue

    ok = False
    host_port = int(info.get('instances').get('ports').get("22/tcp")[0].get('HostPort'))

print("===========================host:\n",info.get('instances').get('public_ipaddr'))

print("===========================ports:\n",info.get('instances').get('ports'))



def generate_inventory(containers: List[Dict[str, str]], output_file: str = 'playbooks/inventory.yml'):
    """
    动态生成Ansible inventory配置
    
    Args:
        containers: 容器信息列表，每个容器是一个字典，包含:
                   - name: 容器名称
                   - host: 主机地址
                   - port: SSH端口
        output_file: 输出文件路径
    """
    
    # 构建hosts字典
    hosts = {}
    for container in containers:
        container_name = container.get('name')
        host = container.get('host', 'localhost')
        port = container.get('port', '22')
        
        hosts[container_name] = {
            'ansible_host': host,
            'ansible_port': port
        }
    
    # 构建完整的inventory结构
    inventory = {
        'all': {
            'children': {
                'docker_containers': {
                    'hosts': hosts,
                    'vars': {
                        'ansible_user': 'ansible',
                        'ansible_ssh_private_key_file': '~/.ssh/id_rsa_ansible',
                        'ansible_python_interpreter': '/usr/bin/python3',
                        'ansible_ssh_common_args': '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
                    }
                }
            }
        }
    }
    
    # 写入YAML文件
    with open(output_file, 'w') as f:
        yaml.dump(inventory, f, default_flow_style=False, sort_keys=False)
    
    print(f"Inventory文件已生成: {output_file}")
    return inventory


generate_inventory([{"name": hostname,"host":host_ip,"port":host_port}])
