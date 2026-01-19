import { Dataset, PlaywrightCrawlingContext } from 'crawlee';

export const youtubeHandler = async ({ page, request, log }: PlaywrightCrawlingContext) => {
    log.info(`正在处理 YouTube 视频: ${request.url}`);

    // 生产级技巧：拦截内部接口，获取最准确的结构化数据
    const responsePromise = page.waitForResponse((res: any) => 
        res.url().includes('youtubei/v1/player')
    );

    await page.goto(request.url, { waitUntil: 'networkidle' });

    try {
        const response = await responsePromise;
        const data = await response.json();
        const details = data.videoDetails;

        await Dataset.pushData({
            platform: 'YouTube',
            id: details.videoId,
            title: details.title,
            viewCount: details.viewCount,
            author: details.author,
            scrapedAt: new Date().toISOString(),
        });
    } catch (err: any) {
        log.error(`YouTube 数据抓取失败: ${err?.message ?? err}`);
    }
};