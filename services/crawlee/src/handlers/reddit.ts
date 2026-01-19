import { Dataset, PlaywrightCrawlingContext, log } from 'crawlee';

/**
 * 递归提取评论数据 (参考 Steel Defensive Parsing 实现)
 */
const extractCommentsRecursively = (childrenList: any[], parentId: string, depth: number = 0): any[] => {
    const extractedData: any[] = [];
    if (!Array.isArray(childrenList)) return [];

    for (const child of childrenList) {
        // 仅处理评论类型 (t1)
        if (child?.kind !== 't1') continue;
        const data = child.data;
        if (!data) continue;

        extractedData.push({
            id: data.id,
            parent_id: parentId,
            author: data.author,
            body: data.body,
            score: data.score,
            created_utc: data.created_utc,
            depth: depth,
            permalink: data.permalink,
        });

        // 递归处理回复
        if (data.replies?.data?.children) {
            extractedData.push(...extractCommentsRecursively(
                data.replies.data.children,
                data.id,
                depth + 1
            ));
        }
    }
    return extractedData;
};

export const redditHandler = async ({ page, request }: PlaywrightCrawlingContext) => {
    // 1. URL 处理：强制转换为 .json 接口以获取结构化数据
    const apiUrl = request.url.includes('.json') ? request.url : `${request.url.replace(/\/$/, '')}.json`;
    
    log.info(`[REDDIT] 正在请求数据: ${apiUrl}`);

    // 2. 执行抓取 (使用 page.goto 配合 JSON 拦截或直接 page.request)
    const response = await page.goto(apiUrl, { waitUntil: 'networkidle' });
    
    if (!response || response.status() !== 200) {
        throw new Error(`Reddit 请求失败: ${response?.status() || 'No Response'}`);
    }

    const json = await response.json();

    // 3. 防御性结构校验
    if (!Array.isArray(json) || json.length < 2) {
        throw new Error(`非法的 Reddit 响应结构: ${request.url}`);
    }

    // 4. 数据解析
    const postData = json[0]?.data?.children?.[0]?.data;
    const rawComments = json[1]?.data?.children || [];

    if (!postData) throw new Error("无法解析帖子内容");

    const allComments = extractCommentsRecursively(rawComments, postData.id);

    // 5. 存储结果
    await Dataset.pushData({
        platform: 'Reddit',
        topic_id: request.userData.topic_id || postData.id,
        source_url: request.url,
        reddit_post_id: postData.id,
        title: postData.title,
        content: postData.selftext,
        comments_count: allComments.length,
        comments: allComments,
        scrapedAt: new Date().toISOString(),
    });

    log.info(`[REDDIT] ✅ 完成 | 评论总数: ${allComments.length}`);
};