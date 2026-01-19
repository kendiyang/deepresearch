import { PlaywrightCrawler } from 'crawlee';
import { router } from '../src/routes.js';

describe('Reddit Handler Integration', () => {
    const redditUrls = [
        'https://www.reddit.com/r/tretinoin/comments/1nl5jv1/facial_oils_for_repairing_skin_barrier',
        'https://www.reddit.com/r/NaturalBeauty/comments/1oojofx/skin_care_secret_to_antiaging_turns_into_rash',
        'https://www.reddit.com/r/NaturalBeauty/comments/1lwgm0e/i_use_a_blend_of_black_seed_oil_castor_oil_and',
        'https://www.reddit.com/r/essentialoils/comments/1lwgoe4/i_use_a_blend_of_black_seed_oil_castor_oil_and',
        'https://www.reddit.com/r/NaturalBeauty/comments/1l2e1zk/whats_that_one_skincare_routine_that_completely',
        'https://www.reddit.com/r/AskWomenOver50/comments/1pxdqgb/best_facial_moisturizer_for_women_over_50',
        'https://www.reddit.com/r/40PlusSkinCare/comments/1pcd8mn/oils_are_working_for_me',
        'https://www.reddit.com/r/SkincareAddiction/comments/1nzs7d3/antiaging_feel_like_i_look_rough_for_29_any',
        'https://www.reddit.com/r/MomForAMinute/comments/1olcyuf/mom_no_one_ever_taught_me_what_moisturizing_is',
        'https://www.reddit.com/r/PlasticSurgery/comments/1ivuhcf/weight_loss_lose_skin_help',
        'https://www.reddit.com/r/beauty/comments/1is42aq/for_those_of_you_over_40_who_dont_want_to_do',
        'https://www.reddit.com/r/redlighttherapy/comments/1kh8eor/45_days_with_no_results_help',
        'https://www.reddit.com/r/over60/comments/1mtorvz/skin_care_for_men',
        'https://www.reddit.com/r/beauty/comments/1jxav10/tell_me_your_holy_grail_moisturizer',
        'https://www.reddit.com/r/Supplements/comments/1iu6ewo/supplements_you_swear_by',
        'https://www.reddit.com/r/fragrance/comments/1jdl45d/does_anybody_else_find_frankincense_to_be_a_sexy',
        'https://www.reddit.com/r/coworkerstories/comments/1mab9ny/coworker_got_into_essential_oils_and_now_its',
        'https://www.reddit.com/r/DIYfragrance/comments/1ml5epr/dangers_of_fragrance_making',
        'https://www.reddit.com/r/perfumesthatfeellike/comments/1oa255m/strong_frankincense_perfumes_that_last_long',
        'https://www.reddit.com/r/breastcancer/comments/1l7gn6g/whats_the_worst_weirdest_advice_youve_received',
        'https://www.reddit.com/r/AskHistorians/comments/1p8mxcs/would_the_israelites_have_attached_any_special',
        'https://www.reddit.com/r/Meditation/comments/1padswz/strange_experiences_after_starting_meditation_is',
        'https://www.reddit.com/r/Incense/comments/1lw8d7b/need_some_help_with_brass_tealight_frankincense',
        'https://www.reddit.com/r/FemFragLab/comments/1ox794d/perfumes_that_have_a_strong_and_lasting',
    ];

    it('should crawl all provided Reddit URLs without error', async () => {
        const crawler = new PlaywrightCrawler({
            requestHandler: router,
            maxRequestRetries: 1,
            maxRequestsPerCrawl: redditUrls.length,
        });
        await crawler.run(redditUrls.map(url => ({ url, label: 'REDDIT' })));
        // 可根据需要添加断言检查 Dataset 或日志
    }, 600000); // 10分钟超时
});
