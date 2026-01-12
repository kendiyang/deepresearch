from curl_cffi import requests

def scrape_forbes_article():
    target_url = "https://medium.com/scrub-me-secrets-the-blog/10-facial-products-for-sensitive-skin-bd6162354a0f"
    
    # 策略关键点：
    # 1. 使用较新的 Chrome 指纹 (chrome120)
    # 2. 必须携带完整的 User-Agent 和 Accept 头，模拟真实浏览行为
    # 3. 启用 follow_redirects，因为 Forbes 可能会有跳转
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",  # 伪造来源，增加可信度
        "Upgrade-Insecure-Requests": "1"
    }

    try:
        # impersonate="chrome120" 是过 Forbes WAF 的核心
        response = requests.get(
            target_url,
            impersonate="chrome120", 
            headers=headers,
            timeout=15
        )
        
        if response.status_code == 200:
            print(f"✅ 成功绕过检测 | 状态码: {response.status_code}")
            print(f"页面标题片段: {response.text[:200].split('<title>')[1].split('</title>')[0]}")
            
            # 这里你可以接着用 BeautifulSoup 解析 response.text
            # 提取具体的 beauty and wellness products 列表
        else:
            print(f"❌ 访问受限 | 状态码: {response.status_code}")
            
    except Exception as e:
        print(f"发生错误: {e}")

if __name__ == "__main__":
    scrape_forbes_article()