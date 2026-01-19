#!/bin/bash
set -e

# 定义标记文件路径（Ansible 任务最后会创建这个文件）
FLAG_FILE="/tmp/provision_complete"

echo "[Wrapper] 正在启动 SSH 服务，等待 Ansible 配置..."
# 确保目录存在
mkdir -p /var/run/sshd
# 后台启动 SSHD
/usr/sbin/sshd

# 循环检查标记文件
echo "[Wrapper] 等待 provisioning.yml 执行完成..."
while [ ! -f "$FLAG_FILE" ]; do
  sleep 2
done

echo "[Wrapper] 检测到配置完成！正在切换至业务进程..."

# 停止 SSH 服务（释放端口和资源）
if [ -f /var/run/sshd.pid ]; then
  kill $(cat /var/run/sshd.pid)
else
  pkill sshd || true
fi

# 加载由 Ansible 写入的环境变量
if [ -f /etc/environment ]; then
  set -a # 自动导出变量
  . /etc/environment
  set +a
fi

# 赋予入口脚本执行权限（双重保险）
chmod +x /app/api/entrypoint.sh

# 关键：使用 exec 替换当前进程。
# 这意味着 nodejs/应用将成为 PID 1，能够接收 Docker 停止信号
exec /app/api/entrypoint.sh