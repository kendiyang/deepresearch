ARG NODE_VERSION=22.13.0
FROM node:${NODE_VERSION}-slim

WORKDIR /app

# 1. 安装 SSH 和 Python3 (Ansible 依赖)
# 同时安装 sudo 以便 Ansible 可以提权（如果是 root 用户则非必须，但推荐）
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server \
    python3 \
    python3-apt \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 2. 配置 SSH
RUN mkdir /var/run/sshd
# 配置SSH - 修正为支持root密码登录
RUN sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?AuthorizedKeysFile.*/AuthorizedKeysFile .ssh\/authorized_keys/' /etc/ssh/sshd_config 
#    && echo "root:test" | chpasswd

# 生成SSH主机密钥（如果不存在）
RUN if [ ! -f /etc/ssh/ssh_host_rsa_key ]; then \
        ssh-keygen -A; \
    fi

# 3. 复制包装脚本
COPY start_wrapper.sh /usr/local/bin/start_wrapper.sh
RUN chmod +x /usr/local/bin/start_wrapper.sh

# 4. 暴露端口
# 22: SSH, 3000: Steel API, 9223: Chrome Debug
EXPOSE 22 3000 9223

# 5. 设置入口
ENTRYPOINT ["/usr/local/bin/start_wrapper.sh"]