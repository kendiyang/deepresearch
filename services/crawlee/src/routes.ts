import { createPlaywrightRouter, PlaywrightCrawlingContext } from 'crawlee';
import { redditHandler } from './handlers/reddit.js';
import { tiktokHandler } from './handlers/tiktok.js';
import { youtubeHandler } from './handlers/youtube.js';
import { confirmTaskFinished } from './utils/redis.js';

export const router = createPlaywrightRouter();

/**
 * 企业级包装器：处理自动 ACK、错误上报和性能统计
 */
const withEnterpriseLifecycle = (handler: Function) => {
    return async (context: PlaywrightCrawlingContext) => {
        const { request, log } = context;
        const start = Date.now();
        
        try {
            log.info(`[开始处理] ${request.label}: ${request.url}`);
            
            await handler(context);
            
            // 成功后执行 ACK (从 Redis 移除)
            if (request.userData.rawTask) {
                await confirmTaskFinished(request.userData.rawTask);
            }
            
            const duration = Date.now() - start;
            log.info(`[任务完成] ${request.label}: ${request.url} (${duration}ms)`);
            
        } catch (err: any) {
            // 记录详细错误日志
            log.error(`[处理异常] ${request.label}: ${request.url} | Error: ${err.message}`);
            
            // 关键：必须抛出异常！
            // 只有抛出异常，Crawlee 才会触发 errorHandler 和重试机制
            throw err; 
        }
    };
};

router.addHandler('REDDIT', withEnterpriseLifecycle(redditHandler));
router.addHandler('TIKTOK', withEnterpriseLifecycle(tiktokHandler));
router.addHandler('YOUTUBE', withEnterpriseLifecycle(youtubeHandler));