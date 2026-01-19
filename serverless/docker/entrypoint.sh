#!/bin/bash
# entrypoint.sh

set -e

# --- 5. 启动基础服务 ---
start_services() {
    # 确保 SSHD 运行目录存在 (Ubuntu/Debian 常见问题)
    if [ ! -d /run/sshd ]; then
        mkdir -p /run/sshd
    fi

    # 启动 syslog (如果安装了)
    if [ "${ENABLE_SYSLOG}" = "true" ] && command -v rsyslogd &> /dev/null; then
        log "Starting rsyslog"
        service rsyslog start || true
    fi
    
    # 启动 cron (如果安装了)
    if [ "${ENABLE_CRON}" = "true" ] && command -v cron &> /dev/null; then
        log "Starting cron"
        service cron start || true
    fi

    return 0
}

# --- 主函数 ---
main() {
    
    # 启动辅助服务
    start_services
    
    # 4. 生成 Ansible facts (如果有该脚本)
    if [ -x /etc/ansible/facts.d/custom.fact ]; then
        log "Generating custom Ansible facts"
        /etc/ansible/facts.d/custom.fact || log "Warning: custom.fact failed"
    fi
    
    log "Container initialization completed. Starting main process..."
    
    # 5. 执行 Docker CMD (通常是 /usr/sbin/sshd -D)
    exec "$@"
}

main "$@"
