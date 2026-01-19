#!/usr/bin/env python3
# ansible_facts.py

import json
import os
import platform
import socket
import psutil
import datetime

def collect_facts():
    facts = {
        "container": {
            "id": os.getenv('HOSTNAME', 'unknown'),
            "runtime": "docker",
            "image": os.getenv('CONTAINER_IMAGE', 'unknown'),
            "hostname": socket.gethostname(),
            "fqdn": socket.getfqdn(),
        },
        "system": {
            "distribution": platform.system(),
            "distribution_version": platform.version(),
            "architecture": platform.machine(),
            "kernel": platform.release(),
            "python_version": platform.python_version(),
        },
        "resources": {
            "cpu_count": psutil.cpu_count(),
            "memory_total_mb": psutil.virtual_memory().total // (1024 * 1024),
            "disk_usage": {},
        },
        "network": {
            "interfaces": {},
            "default_ipv4": {},
            "default_ipv6": {},
        },
        "environment": {
            "variables": {k: v for k, v in os.environ.items() if not k.startswith('_')},
        },
        "timestamp": datetime.datetime.now().isoformat(),
    }
    
    # 收集磁盘信息
    for partition in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(partition.mountpoint)
            facts["resources"]["disk_usage"][partition.mountpoint] = {
                "total_gb": usage.total // (1024**3),
                "used_gb": usage.used // (1024**3),
                "free_gb": usage.free // (1024**3),
                "percent_used": usage.percent
            }
        except:
            pass
    
    # 收集网络信息
    for interface, addrs in psutil.net_if_addrs().items():
        facts["network"]["interfaces"][interface] = []
        for addr in addrs:
            facts["network"]["interfaces"][interface].append({
                "family": str(addr.family),
                "address": addr.address,
                "netmask": addr.netmask,
            })
    
    return facts

if __name__ == "__main__":
    facts = collect_facts()
    print(json.dumps(facts, indent=2))