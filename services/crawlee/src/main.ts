import 'dotenv/config';
import { PlaywrightCrawler, ProxyConfiguration, log } from 'crawlee';
import { router } from './routes.js';
import { startRedisSubscription, fetchSingleTask , confirmTaskFinished } from './utils/redis.js'; // 需新增 fetchSingleTask
import { setupInterceptors } from './utils/interceptors.js';


const proxyConfiguration = new ProxyConfiguration({
    proxyUrls: [process.env.PROXY_URL!],
});

const crawler = new PlaywrightCrawler({
    proxyConfiguration,
    requestHandler: router,
    useSessionPool: true,
    sessionPoolOptions: {
        maxPoolSize: 50,
        sessionOptions: { maxErrorScore: 1 },
    },
    browserPoolOptions: {
        useFingerprints: true, 
        maxOpenPagesPerBrowser: 50, 
        operationTimeoutSecs: 60,
    },
    launchContext: {
        launchOptions: {
            headless: true,
            args: ['--disable-gpu', '--no-sandbox'],
        },
    },
    preNavigationHooks: [
        async ({ page }) => {
            await setupInterceptors(page); 
        },
    ],
    errorHandler: async ({ request, session, log }) => {
        const retryCount = request.retryCount;
        // 计算阶梯延迟：第1次重试等2s，第2次等4s，第3次等6s
        const delayMs = (retryCount + 1) * 2000;

        log.warning(`[重试中] 任务: ${request.label} | 尝试次数: ${retryCount + 1}/3 | 延迟: ${delayMs}ms | URL: ${request.url}`);

        // 关键：退休当前 Session，强制 Crawlee 在下一次请求时分配新的 Session (及新的代理 IP)
        session?.retire();

        // 显式等待，实现阶梯延迟效果
        await new Promise(resolve => setTimeout(resolve, delayMs));
    },
    // 3. 彻底失败处理 (直接丢弃)
    failedRequestHandler: async ({ request, log }) => {
        log.error(`[任务丢弃] 重试耗尽，彻底放弃任务: ${request.url}`);
        
        // 关键修复：从 Redis processing 队列中移除，防止被僵尸检测机制重新复活
        if (request.userData.rawTask) {
            try {
                await confirmTaskFinished(request.userData.rawTask);
                log.info(`[Redis] 已清理失败任务数据`);
            } catch (err) {
                log.error(`[Redis] 清理失败任务时出错`, { error: err });
            }
        }
    },
    
    maxRequestRetries: 3,
    requestHandlerTimeoutSecs: 30,
    autoscaledPoolOptions: {
        desiredConcurrency: 10,
    },
});

/**
 * 核心逻辑：确保队列不为空再启动
 */
async function run() {
    const requestQueue = await crawler.getRequestQueue();

    log.info('🚀 正在等待 Redis 初始任务以激活引擎...');

    // 1. 阻塞循环：直到 Redis 返回第一个任务
    let hasInitialTask = false;
    while (!hasInitialTask) {
        hasInitialTask = await fetchSingleTask(requestQueue);
        if (!hasInitialTask) {
            await new Promise(res => setTimeout(res, 2000));
        }
    }

    log.info('✅ 已获取初始任务，启动 Crawlee 引擎...');

    // 2. 启动后台持续订阅逻辑
    startRedisSubscription(crawler).catch((err) => log.error('Redis 订阅异常', err));

    // 3. 正式启动爬虫
    await crawler.run();
}

run().catch(err => {
    log.error('系统崩溃', err);
    process.exit(1); 
});