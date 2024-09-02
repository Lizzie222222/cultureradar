from flask import Flask, request, jsonify, render_template, send_file
from io import BytesIO
import requests
from bs4 import BeautifulSoup
import openai
import os
from dotenv import load_dotenv
import re
import logging
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from time import time
from urllib.parse import urlparse

load_dotenv()

app = Flask(__name__)
openai.api_key = os.getenv('OPENAI_API_KEY')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/search', methods=['POST'])
def search():
    keyword = request.form['keyword']
    start_time = time()
    try:
        results = scrape_guardian_on_my_radar(keyword)
        if not results:
            logger.warning(f"No articles found for keyword: {keyword}")
            return jsonify({"results": [], "time_taken": 0})
        formatted_results = format_results_with_ai(results, keyword)
        time_taken = time() - start_time
        return jsonify({"results": formatted_results, "time_taken": time_taken})
    except Exception as e:
        logger.exception(f"Error occurred during search for keyword '{keyword}': {str(e)}")
        return jsonify({"error": "An error occurred during the search. Please try again."}), 500

@lru_cache(maxsize=100)
def extract_external_links(html_content):
    soup = BeautifulSoup(html_content, 'lxml')
    links = {}
    content = soup.find('div', class_='content__article-body')
    if content:
        for a in content.find_all('a', attrs={'data-link-name': ['in body link', 'auto-linked-tag']}):
            if not a['href'].startswith(('https://www.theguardian.com', 'http://www.theguardian.com', '/')):
                links[a.text.strip()] = a['href']
        
        keywords = ['recommends', 'recommendation', 'suggests', 'favourite', 'favorite', 'pick']
        for p in content.find_all('p'):
            if any(keyword in p.text.lower() for keyword in keywords):
                for a in p.find_all('a', href=True):
                    if not a['href'].startswith(('https://www.theguardian.com', 'http://www.theguardian.com', '/')):
                        links[a.text.strip()] = a['href']
        
        for p in content.find_all('p'):
            colon_splits = re.split(r':\s*', p.text)
            if len(colon_splits) > 1:
                for a in p.find_all('a', href=True):
                    if not a['href'].startswith(('https://www.theguardian.com', 'http://www.theguardian.com', '/')):
                        links[a.text.strip()] = a['href']
    
    return links

def fetch_url(url):
    response = requests.get(url)
    return response.text

def scrape_guardian_on_my_radar(keyword):
    base_url = 'https://www.theguardian.com'
    url = f'{base_url}/culture/series/on-my-radar'
    
    try:
        html = fetch_url(url)
        soup = BeautifulSoup(html, 'lxml')
    except Exception as e:
        logger.error(f"Error fetching the Guardian page: {e}")
        return []
    
    articles = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_url = {}
        for link in soup.find_all('a', href=True):
            if 'on-my-radar' in link['href']:
                article_url = link['href']
                if article_url.startswith('/'):
                    article_url = base_url + article_url
                future_to_url[executor.submit(fetch_url, article_url)] = article_url
        
        for future in as_completed(future_to_url):
            article_url = future_to_url[future]
            try:
                article_html = future.result()
                article_soup = BeautifulSoup(article_html, 'lxml')
                if keyword.lower() in article_soup.text.lower():
                    title_tag = article_soup.find('h1')
                    title = title_tag.text.strip() if title_tag else "Untitled"
                    
                    image = extract_image(article_soup)
                    snippet = extract_snippet(article_soup, keyword)
                    recommended_by = extract_recommended_by(article_soup)
                    recommender_info = extract_recommender_info(article_soup)
                    external_links = extract_external_links(article_html)
                    
                    logger.info(f"Found article: {title}")
                    
                    articles.append({
                        'title': title,
                        'snippet': snippet,
                        'image': image,
                        'link': article_url,
                        'recommended_by': recommended_by,
                        'recommender_info': recommender_info,
                        'external_links': external_links
                    })
            except Exception as e:
                logger.error(f"Error processing article {article_url}: {e}")
                continue
    
    return articles

@lru_cache(maxsize=100)
def extract_recommender_info(soup):
    paragraphs = soup.find_all('p')
    for p in paragraphs:
        if 'is a' in p.text or 'was a' in p.text:
            return p.text.strip()
    return "No additional information available about the recommender."

@lru_cache(maxsize=100)
def extract_image(soup):
    image = None
    main_content = soup.find('div', class_='content__main-column')
    if main_content:
        img_tag = main_content.find('img', class_='immersive-main-media__media')
        if img_tag:
            image = img_tag.get('src')
    
    if not image:
        script_tag = soup.find('script', type='application/ld+json')
        if script_tag:
            try:
                data = json.loads(script_tag.string)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and 'image' in item:
                            image = item['image'].get('url') if isinstance(item['image'], dict) else item['image']
                            break
                elif isinstance(data, dict):
                    image = data.get('image', {}).get('url') if isinstance(data.get('image'), dict) else data.get('image')
            except json.JSONDecodeError:
                pass
    
    if not image:
        og_image = soup.find('meta', property='og:image')
        if og_image:
            image = og_image.get('content')
    
    if not image:
        body_images = soup.select('.content__article-body img')
        if body_images:
            image = body_images[0].get('src')
    
    if image:
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, str) and not image.startswith('http'):
            image = f"https:{image}"
    
    return image or '/static/placeholder.jpg'

@lru_cache(maxsize=100)
def extract_snippet(soup, keyword):
    paragraphs = soup.find_all('p')
    relevant_paragraphs = [p.text for p in paragraphs if keyword.lower() in p.text.lower()]
    
    if relevant_paragraphs:
        snippet = ' '.join(relevant_paragraphs)
        return snippet[:497] + '...' if len(snippet) > 500 else snippet
    
    meta_description = soup.find('meta', property='og:description')
    if meta_description:
        return meta_description['content']
    
    first_paragraph = soup.find('p')
    if first_paragraph:
        return first_paragraph.text.strip()
    
    return "No relevant information found."

@lru_cache(maxsize=100)
def extract_recommended_by(soup):
    title = soup.find('h1')
    if title:
        title_text = title.text.strip()
        if "On my radar:" in title_text:
            return title_text.split("On my radar:")[1].split("'s cultural highlights")[0].strip()
    return "Unknown"

def format_results_with_ai(results, keyword):
    formatted_results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_result = {executor.submit(format_single_result, result, keyword): result for result in results}
        for future in as_completed(future_to_result):
            formatted_result = future.result()
            if formatted_result:
                formatted_results.append(formatted_result)
    return formatted_results

def format_single_result(result, keyword):
    external_links_str = "\n".join([f"{key}: {value}" for key, value in result['external_links'].items()])
    prompt = f"""Analyze this cultural recommendation about {keyword}:

    Title: {result['title']}
    Snippet: {result['snippet']}
    Link: {result['link']}
    Image: {result['image']}
    External Links:
    {external_links_str}

    1. EXACT name/title of recommended {keyword}.
    2. Name of recommender.
    3. Brief description of recommender.
    4. 3-4 sentence paragraph about the recommendation, including why it was recommended and interesting details.
    5. External link for the recommendation (not to The Guardian, must be real and relevant).

    Format:
    Recommendation: [EXACT name/title]
    Recommended by: [Name]
    Recommender info: [Brief description]
    Snippet: [Detailed paragraph]
    Recommendation link: [Full URL, omit if not found]

    If no relevant information, respond with 'No relevant information'.
    """
    
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant specialized in analyzing cultural recommendations."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=400
        )
        ai_result = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error calling OpenAI API: {e}")
        return None

    if ai_result == "No relevant information":
        return None

    recommendation_match = re.search(r'Recommendation: (.+)', ai_result)
    recommended_by_match = re.search(r'Recommended by: (.+)', ai_result)
    recommender_info_match = re.search(r'Recommender info: (.+)', ai_result)
    snippet_match = re.search(r'Snippet: (.+)', ai_result, re.DOTALL)
    recommendation_link_match = re.search(r'Recommendation link: (.+)', ai_result)
    
    if all([recommendation_match, recommended_by_match, recommender_info_match, snippet_match]):
        recommendation_link = recommendation_link_match.group(1).strip() if recommendation_link_match else None
        
        if recommendation_link and not recommendation_link.startswith(('https://www.theguardian.com', 'http://www.theguardian.com')):
            try:
                parsed_url = urlparse(recommendation_link)
                valid_link = recommendation_link if all([parsed_url.scheme, parsed_url.netloc]) else None
            except:
                valid_link = None
        else:
            valid_link = None
        
        return {
            'title': recommendation_match.group(1).strip(),
            'snippet': snippet_match.group(1).strip(),
            'image': result['image'],
            'recommended_by': recommended_by_match.group(1).strip(),
            'recommender_info': recommender_info_match.group(1).strip(),
            'link': result['link'],
            'recommendation_link': valid_link
        }
    
    return None

@app.route('/proxy_image')
def proxy_image():
    url = request.args.get('url')
    if not url:
        logger.warning("No URL provided for proxy_image")
        return "No URL provided", 400
    
    try:
        response = requests.get(url, timeout=10)
        content = response.content
        content_type = response.headers.get('Content-Type', 'image/jpeg')
        logger.info(f"Successfully proxied image from {url}")
        return send_file(
            BytesIO(content),
            mimetype=content_type,
            as_attachment=False
        )
    except Exception as e:
        logger.error(f"Error proxying image from {url}: {str(e)}")
        return "Error loading image", 500

if __name__ == '__main__':
    app.run(debug=True, port=5006)
