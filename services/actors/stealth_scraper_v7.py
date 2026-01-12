import asyncio
import logging
import json
import random
import re
import os
import hashlib
import mimetypes
import aiofiles
from typing import List, Dict, Optional, Any
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# --- 第三方库 ---
# pip install curl-cffi beautifulsoup4 aiofiles trafilatura
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

# 尝试导入 trafilatura (推荐安装: pip install trafilatura)
try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

# ================= 配置区域 =================
OUTPUT_FILE = "scraped_data.jsonl"
DOWNLOAD_DIR = "downloads"  # 文件保存目录
PROXY_LIST = []             # 代理列表 e.g. ["http://user:pass@ip:port"]
MAX_CONCURRENCY = 3         # 并发数
# ===========================================

# 自动创建下载目录
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("Scraper")

class ProxyManager:
    def __init__(self, proxies: List[str]):
        self.proxies = proxies
    
    def get_proxy(self) -> Optional[str]:
        return random.choice(self.proxies) if self.proxies else None

class UniversalParser:
    """通用解析器：负责从 HTML 中提取结构化数据"""
    
    def _clean_text(self, text: str) -> str:
        if not text: return ""
        return re.sub(r'\s+', ' ', text).strip()

    def _extract_nextjs_data(self, soup: BeautifulSoup) -> Dict:
        script = soup.find("script", id="__NEXT_DATA__", type="application/json")
        if script:
            try: return json.loads(script.string)
            except: pass
        return {}

    def _recursive_find(self, data: Any, target_keys: List[str], results: List[Any]):
        if isinstance(data, dict):
            match = True
            for k in target_keys:
                if k not in data:
                    match = False
                    break
            if match: results.append(data)
            for v in data.values(): self._recursive_find(v, target_keys, results)
        elif isinstance(data, list):
            for item in data: self._recursive_find(item, target_keys, results)

    def parse(self, html: str, url: str) -> Dict:
        soup = BeautifulSoup(html, "html.parser")
        domain = urlparse(url).netloc
        
        result = {
            "url": url,
            "domain": domain,
            "type": "generic",
            "title": "",
            "content": "",
            "reviews": [],
            "total_pages": 0
        }

        # 提取标题
        if soup.title: result["title"] = self._clean_text(soup.title.string)

        # 判断是否为 Trustpilot
        if "trustpilot.com" in domain:
            result["type"] = "review_platform"
            next_data = self._extract_nextjs_data(soup)
            if next_data:
                raw_reviews = []
                self._recursive_find(next_data, ["reviewText", "rating"], raw_reviews)
                if not raw_reviews:
                    self._recursive_find(next_data, ["text", "rating"], raw_reviews)
                
                for item in raw_reviews:
                    text = item.get("reviewText") or item.get("text")
                    if text:
                        result["reviews"].append({
                            "rating": item.get("rating"),
                            "text": text,
                            "date": item.get("dates", {}).get("publishedDate")
                        })
                
                found_pages = []
                def find_key(obj, key):
                    if isinstance(obj, dict):
                        if key in obj: found_pages.append(obj[key])
                        for v in obj.values(): find_key(v, key)
                    elif isinstance(obj, list):
                        for i in obj: find_key(i, key)
                find_key(next_data, "totalPages")
                if found_pages:
                    vals = [int(x) for x in found_pages if str(x).isdigit()]
                    if vals: result["total_pages"] = max(vals)

        else:
            # 通用网页
            result["type"] = "article/general"
            if HAS_TRAFILATURA:
                # trafilatura 能更好地提取正文
                extracted = trafilatura.extract(html, include_comments=False)
                if extracted: result["content"] = extracted
            
            # 兜底方案
            if not result["content"]:
                paras = [self._clean_text(p.get_text()) for p in soup.find_all("p")]
                result["content"] = "\n\n".join([p for p in paras if len(p) > 30])

        return result

class StealthScraper:
    """下载器：支持 HTML 解析与二进制文件流式下载"""
    def __init__(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager
        self.impersonates = ["chrome120", "safari17_0", "chrome110"]
        self.parser = UniversalParser()

    def _get_filename(self, url: str, content_type: str) -> str:
        """优化后的文件名生成逻辑"""
        parsed = urlparse(url)
        path = parsed.path
        filename = os.path.basename(path)

        # 1. 尝试使用 URL 中的文件名
        # 如果文件名存在且有后缀，且不含非法字符
        if filename and "." in filename and len(filename.split(".")[-1]) <= 5:
            # 简单清洗非法字符
            clean_name = re.sub(r'[\\/*?:"<>|]', "", filename)
            return clean_name
        
        # 2. 否则使用 Hash + 猜测后缀
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if not ext: ext = ".bin"
        file_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
        return f"{file_hash}{ext}"

    async def _save_binary(self, response, url: str) -> Dict:
        """
        流式保存二进制文件到本地
        传入 response 对象而非 content 字节
        """
        content_type = response.headers.get("content-type", "").lower()
        filename = self._get_filename(url, content_type)
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        
        file_size = 0
        
        # 流式写入，防止内存溢出
        async with aiofiles.open(filepath, 'wb') as f:
            async for chunk in response.aiter_content():
                await f.write(chunk)
                file_size += len(chunk)
        
        logger.info(f"💾 文件已保存: {filename} ({file_size/1024:.1f} KB)")
        
        return {
            "url": url,
            "type": "file",
            "file_path": filepath,
            "content_type": content_type,
            "size_bytes": file_size
        }

    async def fetch_and_process(self, url: str) -> Optional[Dict]:
        """核心请求方法：支持 stream 模式防止超时"""
        for attempt in range(3):
            proxy = self.proxy_manager.get_proxy()
            impersonate_ver = random.choice(self.impersonates)
            
            try:
                await asyncio.sleep(random.uniform(1, 3))
                
                async with AsyncSession(
                    impersonate=impersonate_ver,
                    headers={"Referer": "https://www.google.com/"},
                    proxies={"http": proxy, "https": proxy} if proxy else None,
                    timeout=300 # 增加超时时间到 5 分钟
                ) as session:
                    # 开启 stream=True，只下载 header 即可开始后续逻辑
                    response = await session.get(url, stream=True)
                    
                    if response.status_code == 200:
                        content_type = response.headers.get("content-type", "").lower()
                        
                        # A. 网页/Json -> 需要手动读取全文并解析
                        if "text/html" in content_type or "application/json" in content_type:
                            content = await response.content # 读取完整内容
                            return self.parser.parse(content.decode('utf-8', errors='ignore'), url)
                        
                        # B. 文件 -> 传入 response 流式下载
                        else:
                            return await self._save_binary(response, url)

                    elif response.status_code in [403, 429]:
                        logger.warning(f"🚫 [{response.status_code}] Retry: {url}")
                        continue
                    elif response.status_code == 404:
                        logger.error(f"❌ 404 Not Found: {url}")
                        return None
            
            except Exception as e:
                logger.error(f"❌ Error {url}: {str(e)}")
                await asyncio.sleep(1)
        
        return None

class CrawlerManager:
    def __init__(self, start_urls: List[str]):
        self.start_urls = start_urls
        self.queue = asyncio.Queue()
        self.seen_urls = set()
        self.scraper = StealthScraper(ProxyManager(PROXY_LIST))

    def _generate_pagination(self, base_url: str, total_pages: int) -> List[str]:
        parsed = urlparse(base_url)
        qs = parse_qs(parsed.query)
        qs.pop('page', None)
        links = []
        for p in range(2, total_pages + 1):
            qs['page'] = [str(p)]
            new_query = urlencode(qs, doseq=True)
            new_url = urlunparse(parsed._replace(query=new_query))
            links.append(new_url)
        return links

    async def worker(self, worker_id: int):
        # 优化：在循环外打开文件，减少 IO 开销
        async with aiofiles.open(OUTPUT_FILE, mode='a', encoding='utf-8') as f:
            while True:
                try:
                    # 等待任务，如果队列长时间为空则可能已经结束
                    url = await asyncio.wait_for(self.queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    if self.queue.empty(): break
                    continue

                if url in self.seen_urls:
                    self.queue.task_done()
                    continue
                self.seen_urls.add(url)

                logger.info(f"👷 [Worker-{worker_id}] 任务: {url}")
                data = await self.scraper.fetch_and_process(url)

                if data:
                    save_data = {
                        "url": data["url"],
                        "type": data["type"],
                        "timestamp": asyncio.get_event_loop().time()
                    }

                    if data["type"] == "file":
                        save_data["local_path"] = data["file_path"]
                        save_data["content_type"] = data["content_type"]
                        save_data["file_size"] = data["size_bytes"]
                    
                    elif data["type"] == "review_platform":
                        save_data["title"] = data["title"]
                        save_data["reviews"] = data["reviews"]
                        # Trustpilot 分页逻辑
                        if data.get("total_pages", 0) > 1 and ("page=" not in url or "page=1" in url):
                            logger.info(f"✨ 发现 {data['total_pages']} 页，生成任务...")
                            new_links = self._generate_pagination(url, data["total_pages"])
                            for link in new_links:
                                if link not in self.seen_urls: await self.queue.put(link)
                    
                    else: # generic article
                        save_data["title"] = data["title"]
                        save_data["content"] = data["content"]

                    # 写入一行 JSONL
                    await f.write(json.dumps(save_data, ensure_ascii=False) + "\n")
                    await f.flush() # 确保写入

                self.queue.task_done()

    async def run(self):
        for url in self.start_urls: await self.queue.put(url)
        workers = [asyncio.create_task(self.worker(i)) for i in range(MAX_CONCURRENCY)]
        await self.queue.join()
        for w in workers: w.cancel()
        logger.info(f"🎉 全部完成。数据已保存至 {OUTPUT_FILE}，文件已保存至 {DOWNLOAD_DIR}/")

if __name__ == "__main__":
    # 示例目标列表
    targets   = [
    "https://pubmed.ncbi.nlm.nih.gov/38127865",
    "https://pubmed.ncbi.nlm.nih.gov/29450138",
    "https://pubmed.ncbi.nlm.nih.gov/32013535",
    "https://pubmed.ncbi.nlm.nih.gov/39612685",
    "https://pubmed.ncbi.nlm.nih.gov/30539810",
    "https://pubmed.ncbi.nlm.nih.gov/37430475",
    "https://pubmed.ncbi.nlm.nih.gov/PMC8098784",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC6544398",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC7330179",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC9268443",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC11481677",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC12669112",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC11193358",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC11876528",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC10603989",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC9548261",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC8300764",
    "https://digitalcommons.unl.edu/context/biotechpapers/article/1029/viewcontent/Khan_ISCIENCE_2022_Genome_structure.pdf",
    "https://reclaim.cdh.ucla.edu/download/papersCollection/HBmjEs/Extraction%20Of%20Essential%20Oil%20And%20Its%20Applications.pdf",
    "https://admisiones.unicah.edu/uploaded-files/FZ4vJL/5OK101/how_to_use-essential-oils.pdf",
    "https://digitalcommons.liberty.edu/cgi/viewcontent.cgi?article=2032&context=honors",
    "https://cornerstone.lib.mnsu.edu/cgi/viewcontent.cgi?article=2077&context=etds",
    "https://repository.najah.edu/bitstreams/b721ecde-6b21-4bbc-809d-849e31e17387/download",
    "https://repository.sustech.edu/jspui/bitstream/123456789/18582/1/Investigation%20of%20Phytochemicals%20From....pdf",
    "https://researchrepository.wvu.edu/cgi/viewcontent.cgi?article=12511&context=etd",
    "https://www.nel.edu/userfiles/articlesnew/NEL370816A06.pdf",
    "https://digitalcommons.csbsju.edu/cgi/viewcontent.cgi?article=1151&context=ur_cscday",
    "https://www.medrxiv.org/lookup/external-ref?access_num=10.3390/biom9110738&link_type=DOI",
    "https://www.medrxiv.org/lookup/external-ref?access_num=10.3390/ijerph17186506&link_type=DOI",
    "https://www.medrxiv.org/lookup/external-ref?access_num=10.1016/j.fochx.2022.100217&link_type=DOI",
    "https://www.medrxiv.org/content/10.1101/2023.09.22.23295947v1.full.pdf",
    "https://www.medrxiv.org/lookup/external-ref?access_num=10.2147/ccid.S286411&link_type=DOI",
    "https://www.medrxiv.org/content/10.1101/2024.01.30.24302041v1.full.pdf",
    "https://www.medrxiv.org/lookup/external-ref?access_num=10.1111/bph.13059&link_type=DOI",
    "https://www.medrxiv.org/lookup/external-ref?access_num=10.3389/fphar.2020.578970&link_type=DOI",
    "https://www.medrxiv.org/lookup/external-ref?access_num=10.1111/wrr.13130&link_type=DOI",
    "https://www.medrxiv.org/lookup/external-ref?access_num=10.3389/fnut.2017.00052&link_type=DOI",
    "https://ca.trustpilot.com/review/www.vitalityextracts.com",
    "https://ca.trustpilot.com/review/vedaoils.com?page=2",
    "https://ie.trustpilot.com/review/www.vitalityextracts.com?page=4",
    "https://uk.trustpilot.com/review/www.vitalityextracts.com?page=2",
    "https://ca.trustpilot.com/review/freshskin.co.uk?page=3",
    "https://ca.trustpilot.com/review/www.planttherapy.com?page=2",
    "https://ca.trustpilot.com/review/beecosmetics.co.uk?page=2",
    "https://ca.trustpilot.com/review/majesticpure.com",
    "https://au.trustpilot.com/review/vedaoils.com?page=7",
    "https://ca.trustpilot.com/review/wholesalebotanics.com?page=8",
    "https://www.vogue.com/article/best-winter-body-oil-rodin-elizabeth-arden",
    "https://www.vogue.com/article/elle-macpherson-beauty-secrets",
    "https://www.vogue.com/article/best-collagen-creams",
    "https://www.vogue.com/article/palo-santo-hair-skincare-fragrance",
    "https://www.vogue.com/article/best-retinol-serums-creams",
    "https://www.vogue.com/article/dark-spot-removal-retin-a-intense-pulsed-light-lasers-and-more",
    "https://www.vogue.com/article/travel-beauty-long-haul-flight-sleep-skin-hydration-tips-body-face-massage-intermittent-fasting",
    "https://www.vogue.com/article/best-facial-sunscreens",
    "https://www.vogue.com/article/best-summer-fragrances",
    "https://www.trendhunter.com/slideshow/rejuvenating-skincare",
    "https://www.trendhunter.com/trends/pure-botanical-face-serum",
    "https://www.trendhunter.com/trends/clarifying-face-elixir",
    "https://www.trendhunter.com/trends/beauty-booster",
    "https://www.trendhunter.com/slideshow/april-2025-cosmetics",
    "https://pdfs.semanticscholar.org/e80a/a91e6d6aa8673b3cd6cec2f0891d0532c906.pdf",
    "https://media.doterra.com/us/en/ebooks/frankincense.pdf",
    "https://ppj.phypha.ir/article-1-1905-en.pdf",
    "https://naturalingredient.org/wp/wp-content/uploads/1377986878.pdf",
    "https://www.rareessencearomatherapy.com/wp-content/uploads/2021/08/Product-Info-EO-Single-Note-Frankincense.pdf",
    "https://www.rangeproducts.com.au/wp-content/uploads/2023/02/ESSENTIAL-OILS-FOR-THE-SKIN-PDF.pdf?srsltid=AfmBOoqKhYiQtBO1JGNfK7wBaEduLIXrGdhbuufLeMwH5K__jSPqLHJZ",
    "https://www.playitforwardsportstherapy.com/wp-content/uploads/2015/08/Frankincense-product-info.pdf",
    "https://www.jintegrativederm.org/api/v1/articles/136390-essential-oils-in-dermatology.pdf",
    "https://article.sciencepublishinggroup.com/pdf/jps.20210902.14",
    "https://naha.org/assets/product-downloads/NAHA_Webinar_Colleen_Quinn_Sept_2020.pdf",
    "https://www.trustpilot.com/review/www.vitalityextracts.com",
    "https://www.trustpilot.com/review/vedaoils.com",
    "https://www.trustpilot.com/review/www.planttherapy.com?page=2",
    "https://www.trustpilot.com/review/beecosmetics.co.uk",
    "https://www.trustpilot.com/review/sevenminerals.com",
    "https://www.trustpilot.com/review/majesticpure.com",
    "https://www.trustpilot.com/review/www.nealsyardremedies.com",
    "https://ie.trustpilot.com/review/freshskin.co.uk?page=5",
    "https://www.trustpilot.com/review/youngliving.com",
    "https://www.trustpilot.com/review/purextracts.co.uk",
    "https://www.trendhunter.com/trends/affordable-clean-skincare",
    "https://research.sabanciuniv.edu/52101/1/Extraction.pdf",
    "https://admisiones.unicah.edu/Resources/uGVgwj/9OK174/essential__oil__guide.pdf",
    "https://sites.bu.edu/bbrain/files/2022/05/placebo-botanical-2.pdf",
    "https://files.achs.edu/mediabank/files/melissa_clanton.pdf",
    "https://researchmgt.monash.edu/ws/portalfiles/portal/423808167/380897839_oa.pdf",
    "https://digitalcommons.liu.edu/cgi/viewcontent.cgi?article=1008&context=brooklyn_fulltext_master_theses",
    "https://www.stonybrookmedicine.edu/sites/default/files/herbal_medicines_interactions-1.pdf",
    "https://cdn.clinicaltrials.gov/large-docs/03/NCT02543203/Prot_SAP_000.pdf",
    "https://jra.jacksonms.gov/browse/uGVgwj/9OK174/EssentialOilGuide.pdf",
    "https://par.nsf.gov/servlets/purl/10043797",
    "https://www.va.gov/WHOLEHEALTHLIBRARY/docs/Clinician-Guide-Dietary-Supplements-for-Pain.pdf",
    "https://ww2.jacksonms.gov/book-search/YFjgtK/2OK045/WellnessGuide101Wrinkles.pdf",
    "https://downloads.regulations.gov/FDA-2019-N-1482-4185/attachment_1.pdf",
    "https://www.parks.pearlandtx.gov/Home/Components/Form/Form/ShowFormFileN?ID=9022f6d9b0564f75bcda178c0bc79324",
    "https://www.sec.gov/Archives/edgar/data/1678746/000114036120017712/filename2.pdf",
    "https://effectivehealthcare.ahrq.gov/sites/default/files/related_files/cer-272-genitourinary-syndrome.pdf",
    "https://jra.jacksonms.gov/libweb/uGVgwj/9OK174/essential__oil__guide.pdf",
    "https://uk.trustpilot.com/review/beecosmetics.co.uk?page=4",
    "https://www.vogue.com/article/chi-the-spa-at-shangri-la-barr-al-jissah",
    "https://www.vogue.com/article/gwyneth-paltrow-skin-care-and-makeup-routine-beauty-secrets",
    "https://www.vogue.com/article/ayond-new-desert-oriented-skincare-brand-wellness",
    "https://www.vogue.com/article/beauty-cures-for-the-summer-gardener",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC10735031",
    "https://www.rjptonline.org/HTMLPaper.aspx?Journal=Research%20Journal%20of%20Pharmacy%20and%20Technology;PID=2024-17-5-71",
    "https://www.researchgate.net/publication/376749888_Protective_potential_of_frankincense_essential_oil_and_its_loaded_solid_lipid_nanoparticles_against_UVB-induced_photodamage_in_rats_via_MAPK_and_PI3KAKT_signaling_pathways_A_promising_anti-aging_thera",
    "https://manukarx.co.nz/blogs/news/frankincense-oil-benefits",
    "https://www.annmariegianni.com/frankincense-oil-for-wrinkles?srsltid=AfmBOorcc1QWJNOK8LwS8r5IQKSafYpuTzTIdksmHsEMI4t3alsI1hLK",
    "https://draxe.com/essential-oils/what-is-frankincense",
    "https://hiqili.com/blogs/wellness/how-to-dilute-frankincense-oil-for-skin?srsltid=AfmBOopjAM7O-BwLyb-_CgM7hR4xX0cJt0gWZLvEjQ7h-UkA7TTZGtfZ",
    "https://www.bcalm.co.uk/blogs/news/the-amazing-qualities-of-organic-frankincense-essential-oil?srsltid=AfmBOorCD0Tw6hzkXtkltRYQ4lMKi51S32S1-gngdjm8moyqnhLfCQyd",
    "https://www.rockymountainoils.com/pages/frankincense-oil-benefits-uses?srsltid=AfmBOooVfdPFs1kvDDR8d0iZ8wzG9lqOrxIYhmDR9nEhbfKbMk5KwZGN",
    "https://www.bmvfragrances.com/news-blogs/frankincense-oil-a-natural-anti-ageing-solution-for-youthful-and-radiant-skin",
    "https://ca.trustpilot.com/review/pearldeflore.com",
    "https://ca.trustpilot.com/review/cellexialabs.com",
    "https://ca.trustpilot.com/review/strivectin.com",
    "https://www.trustpilot.com/review/www.peterthomasroth.com",
    "https://www.trustpilot.com/review/theperfectcosmetics.co",
    "https://ie.trustpilot.com/review/wrinklesystem.com",
    "https://nz.trustpilot.com/review/pearldeflore.com?page=3",
    "https://nz.trustpilot.com/review/cellexialabs.com?page=5",
    "https://nz.trustpilot.com/review/theperfectcosmetics.co?page=5",
    "https://ca.trustpilot.com/review/musely.com",
    "https://www.vogue.com/article/best-under-eye-patches",
    "https://www.vogue.com/article/best-eye-cream-for-dark-circles",
    "https://www.cosmoprof.com/en/media-room/news/cosmoprof-and-cosmopack-awards-asia-2025-discover-the-winners",
    "https://www.cosmoprof.com/en/media-room/news/discover-the-winners-of-the-2024-cosmoprof-and-cosmopack-asia-awards",
    "https://www.cosmoprof.com/media/cosmoprof/2025/Progetti%20Speciali/BROCHURE_PROGETTI_SOSTENIBILITA__250310.pdf",
    "https://www.cosmoprof.com/media/cosmoprof/2023/news/_11_07_CPA_CTReport_2022_MASTER_compressed.pdf",
    "https://www.cosmoprof.com/media/cosmoprof/2022/news/BEAUTYSTREAMS_CPNA_Report-1.pdf",
    "https://www.cosmoprof.com/media/cosmoprof/2022/CosmoTrends/Report/CosmoTrends_report_part1.pdf",
    "https://www.cosmoprof.com/media/cosmoprof/Country%20Pavilion/2019/Cpbo_19_Poland.pdf",
    "https://www.cosmoprof.com/en/media-room/news/the-keys-to-success-for-cosmoprof-awards-winners",
    "https://www.cosmoprof.com/media/cosmoprof/cosmoprime/Special%20areas%20Brochure/brochureweb_cosmoprof_EGGreen_2019.pdf",
    "https://www.trendhunter.com/slideshow/september-2025-cosmetics",
    "https://www.trendhunter.com/protrends/spiritual-cosmetic",
    "https://www.trendhunter.com/megatrend/youthfulness",
    "https://www.trendhunter.com/trends/regenerative-skin-care",
    "https://www.trendhunter.com/slideshow/september-2025-fashion",
    "https://ascpd.org.au/wp-content/uploads/ASCD-Journal-Edition-10-Cosmeceuticals.pdf",
    "https://www.researchgate.net/publication/373402145_Effectiveness_and_Tolerance_of_Multi-Corrective_Topical_Treatment_for_Infraorbital_Dark_Circles_and_Puffiness/fulltext/64e9f1650453074fbdb437e0/Effectiveness-and-Tolerance-of-Multi-Corrective_Topical-Treatment-for-Infraorbital_Dark_Circles-and-Puffiness.pdf",
    "https://www.cathaypacific.com/content/dam/focal-point/cx/products/emporium/2020q3/emp_20q3_emagazine_full_12mb.pdf",
    "https://eadv.org/wp-content/uploads/scientific-abstracts/EADV-congress-2024/Corrective-aesthetic-and-cosmetic-dermatology.pdf",
    "https://www.cosmoprof-asia.com/wp-content/uploads/2023/10/Cosmoprof-Asia-2023-Korean-Pavilion_FinalLR.pdf",
    "https://www.lifeextension.com/-/media/lifeextension/pdf/magazine/2017/9.pdf?rev=e33f21c0eb39479cb50a27db4f5e0e6e&srsltid=AfmBOordodx7Be9O5e6vG6dX1VskCTZfOBLJW_XNoUrJi0a1EWXfggvm",
    "https://www.lifeextension.asia/pub/media/magefan_blog/52.pdf",
    "https://www.asx.com.au/asxpdf/20100423/pdf/31pygl8vv7l5rg.pdf",
    "https://cosmoprofnorthamerica.com/wp-content/uploads/BEAUTYSTREAMS_CPNA_Report-1.pdf",
    "https://sg.cmbi.com/upload/202206/20220621725871.pdf",
    "https://www.mckinsey.com/~/media/mckinsey/business%20functions/strategy%20and%20corporate%20finance/our%20insights/mckinsey%20on%20finance%20number%2080/mckinsey-on-finance-number-80.pdf",
    "https://ca.trustpilot.com/review/theordinary.com",
    "https://ie.trustpilot.com/review/theordinary.com?page=5",
    "https://uk.trustpilot.com/review/theordinary.com?page=4",
    "https://ca.trustpilot.com/review/adorecosmetics.com",
    "https://ca.trustpilot.com/review/dor24k.com",
    "https://ca.trustpilot.com/review/cellexialabs.com?page=9",
    "https://ca.trustpilot.com/review/upcirclebeauty.com",
    "https://ca.trustpilot.com/review/beautyfrombees.ca",
    "https://ca.trustpilot.com/review/www.freshlycosmetics.com",
    "https://www.thinkwithgoogle.com/intl/en-emea/marketing-strategies/video/almarai-youtube-ramadan-ai-ads",
    "https://www.businessinsider.com/sitemap/2021-02.xml"
]

    # Windows 平台必须设置此 Policy 才能支持 asyncio + curl_cffi
    try:
        import sys
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except ImportError:
        pass

    crawler = CrawlerManager(targets)
    asyncio.run(crawler.run())