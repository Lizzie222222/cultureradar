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

load_dotenv()  # Load environment variables from .env file

app = Flask(__name__)

# Load OpenAI API key from environment variable
openai.api_key = os.getenv('OPENAI_API_KEY')

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/search', methods=['POST'])
def search():
    keyword = request.form['keyword']
    try:
        results = scrape_guardian_on_my_radar(keyword)
        if not results:
            logger.warning(f"No articles found for keyword: {keyword}")
            return jsonify([])
        formatted_results = format_results_with_ai(results, keyword)
        return jsonify(formatted_results)  # Return all results
    except Exception as e:
        logger.exception(f"Error occurred during search for keyword '{keyword}': {str(e)}")
        return jsonify({"error": "An error occurred during the search. Please try again."}), 500

def extract_all_links(soup):
    links = {}
    content = soup.find('div', class_='content__article-body')
    if content:
        for p in content.find_all('p', class_='dcr-uj7d5w'):
            for a in p.find_all('a', href=True):
                links[a.text.strip()] = a['href']
    return links

def scrape_guardian_on_my_radar(keyword):
    base_url = 'https://www.theguardian.com'
    url = f'{base_url}/culture/series/on-my-radar'
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
    except requests.RequestException as e:
        logger.error(f"Error fetching the Guardian page: {e}")
        return []
    
    articles = []
    for link in soup.find_all('a', href=True):
        if 'on-my-radar' in link['href']:
            article_url = link['href']
            if article_url.startswith('/'):
                article_url = base_url + article_url
            
            try:
                article_response = requests.get(article_url)
                article_response.raise_for_status()
                article_soup = BeautifulSoup(article_response.text, 'html.parser')
            except requests.RequestException as e:
                logger.error(f"Error fetching article {article_url}: {e}")
                continue
            
            if keyword.lower() in article_soup.text.lower():
                try:
                    title_tag = article_soup.find('h1')
                    title = title_tag.text.strip() if title_tag else "Untitled"
                    
                    image = extract_image(article_soup)
                    snippet = extract_snippet(article_soup, keyword)
                    recommended_by = extract_recommended_by(article_soup)
                    recommender_info = extract_recommender_info(article_soup)
                    all_links = extract_all_links(article_soup)
                    
                    logger.info(f"Found article: {title}")
                    logger.info(f"Image URL: {image}")
                    logger.info(f"All links: {all_links}")
                    
                    articles.append({
                        'title': title,
                        'snippet': snippet,
                        'image': image,
                        'link': article_url,
                        'recommended_by': recommended_by,
                        'recommender_info': recommender_info,
                        'all_links': all_links
                    })
                except Exception as e:
                    logger.error(f"Error processing article {article_url}: {e}")
                    continue
    
    return articles

def extract_recommender_info(soup):
    # Try to find a paragraph that mentions the recommender
    paragraphs = soup.find_all('p')
    for p in paragraphs:
        if 'is a' in p.text or 'was a' in p.text:
            return p.text.strip()
    
    # If no specific paragraph found, return a default message
    return "No additional information available about the recommender."

def extract_image(soup):
    image = None
    # Method 1: Look for the main content image
    main_content = soup.find('div', class_='content__main-column')
    if main_content:
        img_tag = main_content.find('img', class_='immersive-main-media__media')
        if img_tag:
            image = img_tag.get('src')
    
    # Method 2: Look for structured data
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
    
    # Method 3: Look for OpenGraph image
    if not image:
        og_image = soup.find('meta', property='og:image')
        if og_image:
            image = og_image.get('content')
    
    # Method 4: Look for any image in the article body
    if not image:
        body_images = soup.select('.content__article-body img')
        if body_images:
            image = body_images[0].get('src')
    
    # Ensure the image URL is a string and absolute
    if image:
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, str) and not image.startswith('http'):
            image = f"https:{image}"
    
    return image or '/static/placeholder.jpg'

def extract_snippet(soup, keyword):
    # First, try to find paragraphs containing the keyword
    paragraphs = soup.find_all('p')
    relevant_paragraphs = [p.text for p in paragraphs if keyword.lower() in p.text.lower()]
    
    if relevant_paragraphs:
        # Join the relevant paragraphs, but limit to 500 characters
        snippet = ' '.join(relevant_paragraphs)
        if len(snippet) > 500:
            snippet = snippet[:497] + '...'
        return snippet
    
    # If no relevant paragraphs found, fall back to the meta description or first paragraph
    meta_description = soup.find('meta', property='og:description')
    if meta_description:
        return meta_description['content']
    
    first_paragraph = soup.find('p')
    if first_paragraph:
        return first_paragraph.text.strip()
    
    return "No relevant information found."

def extract_recommended_by(soup):
    title = soup.find('h1')
    if title:
        title_text = title.text.strip()
        if "On my radar:" in title_text:
            return title_text.split("On my radar:")[1].split("'s cultural highlights")[0].strip()
    return "Unknown"

def format_results_with_ai(results, keyword):
    formatted_results = []
    for result in results:
        prompt = f"""You are reviewing an article about a famous person's cultural recommendations. 
        Extract detailed information about {keyword} from the following article:

        Title: {result['title']}
        Snippet: {result['snippet']}
        Link: {result['link']}
        Image: {result['image']}

        The title must be the specific name of the recommended {keyword} from the article. 
        Also, find the name of the person giving the recommendation and a brief description of who they are.
        Choose the most relevant link from the following list to be used as the recommendation link:
        {json.dumps(result['all_links'])}

        If no relevant information is found, say 'No relevant information'.
        Format the response as:
        Title: [Recommended {keyword}]
        Recommended by: [Name of the person giving the recommendation]
        Recommender info: [Brief description of the person giving the recommendation]
        Snippet: [Relevant information or direct quotes about the recommendation, excluding the recommendation link]
        Recommended link: [The full URL of the most relevant link from the provided list]
        """
        
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=200
        )
        ai_result = response.choices[0].message['content'].strip()
        
        if ai_result == "No relevant information":
            continue

        # Extract information from AI result
        title_match = re.search(r'Title: (.+)', ai_result)
        recommended_by_match = re.search(r'Recommended by: (.+)', ai_result)
        recommender_info_match = re.search(r'Recommender info: (.+)', ai_result)
        snippet_match = re.search(r'Snippet: (.+)', ai_result, re.DOTALL)
        recommended_link_match = re.search(r'Recommended link: (.+)', ai_result)
        
        if all([title_match, recommended_by_match, recommender_info_match, snippet_match, recommended_link_match]):
            recommendation_link = recommended_link_match.group(1).strip()
            # Ensure the link is a full URL
            if not recommendation_link.startswith(('http://', 'https://')):
                recommendation_link = f"https://{recommendation_link}"
            
            formatted_results.append({
                'title': title_match.group(1).strip(),
                'snippet': snippet_match.group(1).strip(),
                'image': result['image'],
                'recommended_by': recommended_by_match.group(1).strip(),
                'recommender_info': recommender_info_match.group(1).strip(),
                'link': result['link'],
                'recommendation_link': recommendation_link
            })

    return formatted_results

@app.route('/proxy_image')
def proxy_image():
    url = request.args.get('url')
    if not url:
        logger.warning("No URL provided for proxy_image")
        return "No URL provided", 400
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()  # Raise an exception for bad status codes
        logger.info(f"Successfully proxied image from {url}")
        return send_file(
            BytesIO(response.content),
            mimetype=response.headers.get('Content-Type', 'image/jpeg'),
            as_attachment=False
        )
    except requests.RequestException as e:
        logger.error(f"Error proxying image from {url}: {str(e)}")
        return "Error loading image", 500

if __name__ == '__main__':
    app.run(debug=True, port=5006)
