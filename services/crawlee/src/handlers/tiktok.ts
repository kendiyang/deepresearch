import { Dataset, PlaywrightCrawlingContext } from 'crawlee';

export const tiktokHandler = async ({ page, request, log }: PlaywrightCrawlingContext) => {
    // 拦截逻辑
    const rawData = await page.evaluate(() => {
        // 企业级优化：不依赖 ID，通过内容特征寻找 JSON
        const scripts = Array.from(document.querySelectorAll('script'));
        const target = scripts.find(s => s.textContent?.includes('webapp__all_data__'));
        return target ? JSON.parse(target.textContent!) : null;
    });

    const videoInfo = rawData?.webapp__all_data__?.itemInfo?.itemStruct;

    if (!videoInfo) {
        // 触发自愈逻辑：标记页面状态异常，强制重试
        throw new Error('TikTok 状态解析为空，可能遭遇人机验证或结构改版');
    }

    await Dataset.pushData({
        platform: 'TikTok',
        data: videoInfo,
        scrapedAt: new Date().toISOString(),
    });
};