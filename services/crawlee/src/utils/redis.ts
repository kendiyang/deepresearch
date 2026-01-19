import { Redis } from 'ioredis';
import { PlaywrightCrawler, log } from 'crawlee';

const redis = new Redis(process.env.REDIS_URL || 'redis://localhost:6379');
const MAIN_QUEUE = 'crawler:tasks';
const PROCESSING_QUEUE = 'crawler:tasks:processing';
const TASK_TIMEOUT = 180; // 3分钟超时

export const startRedisSubscription = async (crawler: PlaywrightCrawler) => {
    const { requestQueue } = crawler;

    // 守护进程：定期清理僵尸任务 (Sentinel)
    setInterval(async () => {
        const tasks = await redis.lrange(PROCESSING_QUEUE, 0, -1);
        const now = Date.now();
        for (const rawTask of tasks) {
            try {
                const task = JSON.parse(rawTask);
                // 只有超时严重才恢复，避免与正常的重试机制冲突
                // 注意：由于我们在 main.ts 做了阶梯重试（2+4+6=12s），这里的超时时间(180s)是安全的
                if (now - (task.timestamp || 0) > TASK_TIMEOUT * 1000) {
                    await redis.lrem(PROCESSING_QUEUE, 1, rawTask);
                    await redis.lpush(MAIN_QUEUE, rawTask);
                    log.warning(`[Redis] 僵尸任务已恢复: ${task.url}`);
                }
            } catch (e) {
                log.error('[Redis] 僵尸任务解析失败', { rawTask });
            }
        }
    }, 30000);

    // 持续拉取新任务
    while (true) {
        try {
            const queueInfo = await requestQueue?.getInfo();
            // 背压控制：本地队列堆积超过 100 时暂停拉取
            if (queueInfo && queueInfo.pendingRequestCount > 100) {
                await new Promise((res) => setTimeout(res, 5000));
                continue;
            }

            const rawTask = await redis.brpoplpush(MAIN_QUEUE, PROCESSING_QUEUE, 10);
            if (!rawTask) continue;

            const task = JSON.parse(rawTask);
            await requestQueue?.addRequest({
                url: task.url,
                label: task.label,
                // 注入 rawTask 用于后续 ACK/NACK
                userData: { ...task.userData, rawTask, timestamp: Date.now() },
                uniqueKey: task.uniqueKey || task.url,
            });
        } catch (err: any) {
            log.error('Redis 事务拉取异常', { message: err.message });
            await new Promise((res) => setTimeout(res, 5000));
        }
    }
};

/**
 * 确认任务结束（成功或彻底失败后调用）
 * 从 Processing 队列中移除
 */
export const confirmTaskFinished = async (rawTask: string) => {
    await redis.lrem(PROCESSING_QUEUE, 1, rawTask);
};

export const fetchSingleTask = async (requestQueue: any): Promise<boolean> => {
    try {
        const rawTask = await redis.brpoplpush(MAIN_QUEUE, PROCESSING_QUEUE, 2);
        if (!rawTask) return false;

        const task = JSON.parse(rawTask);
        await requestQueue.addRequest({
            url: task.url,
            label: task.label,
            userData: { ...task.userData, rawTask, timestamp: Date.now() },
            uniqueKey: task.uniqueKey || task.url,
        });
        return true;
    } catch (err) {
        log.error('读取初始任务失败', { error: err instanceof Error ? err.message : String(err) });
        return false;
    }
};