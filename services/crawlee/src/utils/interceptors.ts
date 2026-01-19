import { Page } from 'playwright';

/**
 * 生产级资源拦截器
 * 屏蔽图片、视频、字体以及常见的广告/分析追踪器
 */
export const setupInterceptors = async (page: Page) => {
    let totalBytes = 0;

    await page.route('**/*', async (route) => {
        const request = route.request();
        const type = request.resourceType();

        // 极致性能：除了 fetch/xhr/document 全部拦截
        if (['image', 'media', 'font', 'stylesheet', 'other'].includes(type)) {
            return route.abort();
        }

        // 统计流量 (仅用于监控)
        const response = await route.fetch().catch(() => null);
        if (response) {
            const buffer = await response.body();
            totalBytes += buffer.length;
            await route.fulfill({ response, body: buffer });
        } else {
            await route.continue();
        }
    });
};