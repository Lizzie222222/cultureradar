from flask import Flask, request, jsonify, render_template, send_file
from io import BytesIO
import requests
from bs4 import BeautifulSoup
import openai
import os
from dotenv import load_dotenv
import re
import logging

load_dotenv()  # Load environment variables from .env file

app = Flask(__name__)

# Load OpenAI API key from environment variable
openai.api_key = os.getenv('OPENAI_API_KEY')

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
        return jsonify(formatted_results)
    except Exception as e:
        logger.exception(f"Error occurred during search for keyword '{keyword}': {str(e)}")
        return jsonify({"error": "An error occurred during the search. Please try again."}), 500

def scrape_guardian_on_my_radar(keyword):
    base_url = 'https://www.theguardian.com'
    url = f'{base_url}/culture/series/on-my-radar'
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    articles = []
    for link in soup.find_all('a', href=True):
        if 'on-my-radar' in link['href']:
            article_url = link['href']
            if article_url.startswith('/'):
                article_url = base_url + article_url
            article_response = requests.get(article_url)
            article_soup = BeautifulSoup(article_response.text, 'html.parser')
            if keyword.lower() in article_soup.text.lower():
                title_tag = article_soup.find('h1')
                
                # Try multiple methods to find the image
                image = None
                image_tag = article_soup.find('img', attrs={'data-gu-contenttype': 'image'})
                if image_tag:
                    image = image_tag.get('src') or image_tag.get('data-src')
                if not image:
                    picture_tag = article_soup.find('picture')
                    if picture_tag:
                        source_tag = picture_tag.find('source', attrs={'type': 'image/jpeg'})
                        if source_tag:
                            image = source_tag.get('srcset', '').split(' ')[0]
                if image and not image.startswith('http'):
                    image = f"https:{image}"
                
                title = title_tag.text.strip() if title_tag else "Untitled"
                snippet = article_soup.text.strip()  # Use the full article text
                
                articles.append({
                    'title': title,
                    'snippet': snippet,
                    'image': image,
                    'link': article_url
                })
    print(f"Scraped {len(articles)} articles containing the keyword '{keyword}'")
    return articles

def format_results_with_ai(results, keyword):
    formatted_results = []
    synonyms = {
        'film': ['movie', 'cinema'],
        'movie': ['film', 'cinema'],
        'tv': ['television', 'series', 'show'],
        'music': ['song', 'album', 'artist'],
        'book': ['novel', 'literature']
    }

    keyword_list = [keyword] + synonyms.get(keyword.lower(), [])
    keyword_string = ', '.join(f"'{k}'" for k in keyword_list)

    for result in results:
        if len(formatted_results) >= 3:
            break

        prompt = f"""You are reviewing an article about a famous person's cultural recommendations. 
        Extract information about {keyword_string} from the following article:

        Title: {result['title']}
        Snippet: {result['snippet']}
        Link: {result['link']}
        Image: {result['image']}

        The title must be the recommended {keyword_string} from the article. 
        Also, find the name of the person giving the recommendation.
        If no relevant information is found, say 'No relevant information'.
        Format the response as:
        Title: [Recommended {keyword}]
        Recommended by: [Name of the person giving the recommendation]
        Snippet: [Relevant information about the recommendation]
        """
        print(f"Sending prompt to OpenAI: {prompt}")
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150
        )
        ai_result = response.choices[0].message['content'].strip()
        print(f"Received AI result: {ai_result}")
        
        if ai_result == "No relevant information":
            continue  # Skip this result if no relevant information is found

        # Extract title, recommended_by, and snippet from AI result
        title_match = re.search(r'Title: (.+)', ai_result)
        recommended_by_match = re.search(r'Recommended by: (.+)', ai_result)
        snippet_match = re.search(r'Snippet: (.+)', ai_result, re.DOTALL)
        
        if title_match and recommended_by_match and snippet_match:
            title = title_match.group(1).strip()
            recommended_by = recommended_by_match.group(1).strip()
            snippet = snippet_match.group(1).strip()
            
            formatted_results.append({
                'title': title,
                'snippet': snippet,
                'image': result['image'],
                'recommended_by': recommended_by,
                'link': result['link']
            })

    return formatted_results  # Return only the relevant results (up to 3)

@app.route('/proxy_image')
def proxy_image():
    url = request.args.get('url')
    if not url:
        return "No URL provided", 400
    
    response = requests.get(url)
    return send_file(BytesIO(response.content), mimetype=response.headers['Content-Type'])

if __name__ == '__main__':
    app.run(debug=True, port=5006)
