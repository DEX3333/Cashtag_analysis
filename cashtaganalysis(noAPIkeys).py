import tweepy
import re
import json
import time
from datetime import datetime, timedelta
import pandas as pd
from collections import defaultdict
import requests
import os
from concurrent.futures import ThreadPoolExecutor

# Twitter API credentials - you'll need to obtain these
# from the Twitter Developer Portal
# Twitter API credentials
TWITTER_API_KEY = "your_api_key_here"  # Consumer Key
TWITTER_API_SECRET = "your_api_secret_here"  # Consumer Secret
TWITTER_ACCESS_TOKEN = "your_access_token_here"
TWITTER_ACCESS_SECRET = "your_access_token_secret_here"
TWITTER_BEARER_TOKEN = "your_bearer_token_here"


# Claude API credentials
CLAUDE_API_KEY = "your_claude_api_key_here"
CLAUDE_API_URL = "your_claude_api_url_here"

# List of exchanges to track mentions of
EXCHANGES = [
    "coinbase", "binance", "kraken", "kucoin", "huobi", "okx", "bybit",
    "hyperliquid", "uniswap", "sushiswap", "pancakeswap", "curve", 
    "balancer", "dydx", "gmx", "1inch", "jupiter", "raydium"
]

# Define regex patterns
CASHTAG_PATTERN = r'\$([A-Za-z0-9]+)'
EXCHANGE_PATTERNS = {exchange: re.compile(r'\b' + exchange + r'\b', re.IGNORECASE) for exchange in EXCHANGES}

# File to store previously seen cashtags
CASHTAG_HISTORY_FILE = "cashtag_history.json"
RESULTS_FILE = "new_listings.csv"
ANALYSIS_FILE = "ai_analysis.csv"

# Number of tweets to collect for AI analysis
ANALYSIS_TWEET_COUNT = 20

def load_cashtag_history():
    try:
        with open(CASHTAG_HISTORY_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_check": None, "seen_cashtags": {}}

def save_cashtag_history(history):
    with open(CASHTAG_HISTORY_FILE, 'w') as f:
        json.dump(history, f)

def authenticate_twitter():
    # Use App-Only Authentication with Bearer Token
    client = tweepy.Client(bearer_token=TWITTER_BEARER_TOKEN)
    return client

def search_twitter(client, query, start_time=None, max_results=100):
    tweets = []
    try:
        for response in tweepy.Paginator(
            client.search_recent_tweets,
            query=query,
            start_time=start_time,
            max_results=max_results,
            tweet_fields=["created_at", "text", "public_metrics"],
            expansions=["author_id"],
            user_fields=["username", "name", "public_metrics"],
            limit=5  # Limit to 5 pages to avoid rate limits
        ):
            if response.data:
                tweets.extend(response.data)
    except tweepy.TooManyRequests:
        print("Rate limit exceeded. Waiting for 15 minutes...")
        time.sleep(15 * 60)
    except Exception as e:
        print(f"Error searching Twitter: {e}")
    
    return tweets

def extract_cashtags(text):
    return re.findall(CASHTAG_PATTERN, text)

def detect_exchange_mentions(text):
    mentions = []
    for exchange, pattern in EXCHANGE_PATTERNS.items():
        if pattern.search(text):
            mentions.append(exchange)
    return mentions

def analyze_with_claude(ticker, collected_tweets, exchanges_mentioned):
    """
    Use Claude to analyze a collection of tweets about a specific cashtag
    and provide insights.
    
    Args:
        ticker (str): The cryptocurrency ticker
        collected_tweets (list): List of tweet texts
        exchanges_mentioned (list): Exchanges mentioned with this cashtag
    
    Returns:
        dict: Claude's analysis of the cashtag
    """
    # Join tweets with newlines and limit to reasonable length
    combined_text = "\n\n".join([t[:500] for t in collected_tweets])
    
    # Create prompt for Claude
    prompt = f"""
Below are {len(collected_tweets)} tweets mentioning the cryptocurrency ${ticker} 
and exchanges including {', '.join(exchanges_mentioned)}. 

As a cryptocurrency analyst, please provide the following information:
1. Is this likely a new listing or just discussion of an existing token?
2. Sentiment score (-5 to +5) based on these tweets
3. Key points mentioned about the token (use bullets)
4. Potential red flags or warning signs, if any
5. Exchange listing status (rumored, confirmed, etc.)
6. Overall recommendation (Investigate Further, Ignore, High Interest)

Tweets:
{combined_text}

Format your analysis as a JSON with the following fields:
- likely_new_listing: boolean
- sentiment_score: number
- key_points: list of strings
- red_flags: list of strings
- listing_status: string
- recommendation: string
- brief_summary: string (100 words max)
"""

    # Call Claude API
    try:
        headers = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "x-api-key": CLAUDE_API_KEY
        }
        
        
        
        data = {
            "model": "claude-3-5-sonnet-20240620",  # Updated to the working model
            "max_tokens": 1000,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
        
        
        response = requests.post(
            CLAUDE_API_URL,
            headers=headers,
            json=data,
            timeout=30
        )
        
        if response.status_code == 200:
            response_data = response.json()
            ai_response = response_data.get("content", [{}])[0].get("text", "")
            
            # Try to extract JSON from the response
            try:
                # Find JSON content (it might be surrounded by markdown code blocks)
                json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", ai_response)
                if json_match:
                    json_content = json_match.group(1)
                else:
                    json_content = ai_response
                
                analysis = json.loads(json_content)
                return analysis
            except json.JSONDecodeError:
                # If Claude didn't return proper JSON, create a basic structure
                return {
                    "likely_new_listing": None,
                    "sentiment_score": None,
                    "key_points": ["Error parsing Claude response"],
                    "red_flags": ["Could not obtain structured analysis"],
                    "listing_status": "unknown",
                    "recommendation": "Investigate Further",
                    "brief_summary": "Could not analyze tweets properly. Please review manually."
                }
        else:
            print(f"Error calling Claude API: {response.status_code} - {response.text}")
            return {
                "likely_new_listing": None,
                "sentiment_score": None,
                "key_points": [],
                "red_flags": ["Error calling Claude API"],
                "listing_status": "unknown",
                "recommendation": "Investigate Further",
                "brief_summary": f"API error: {response.status_code}"
            }
    except Exception as e:
        print(f"Exception in Claude analysis: {e}")
        return {
            "likely_new_listing": None,
            "sentiment_score": None, 
            "key_points": [],
            "red_flags": [f"Exception: {str(e)}"],
            "listing_status": "unknown",
            "recommendation": "Investigate Further",
            "brief_summary": "Error during analysis"
        }

def collect_additional_tweets(client, ticker, max_count=10):
    """
    Collect additional tweets about a specific cashtag for more comprehensive analysis.
    Modified to work with Basic tier API access.
    """
    # Instead of using $ symbol which triggers cashtag operator,
    # search for the ticker as regular text
    query = f"{ticker} -is:retweet lang:en"
    tweets = search_twitter(client, query, max_results=max_count)
    
    if tweets:
        print(f"  Collected {len(tweets)} additional tweets about {ticker}")
    else:
        print(f"  No additional tweets found for {ticker}")
        
    return [tweet.text for tweet in tweets]

def main():
    # Load history
    history = load_cashtag_history()
    
    # Set start time for search - with Basic tier, optimize for fewer API calls
    if history["last_check"]:
        # Use a 6-hour window instead of since last check to reduce API calls
        start_time = datetime.now() - timedelta(hours=6)
    else:
        # Default to 12 hours ago (reduced from 24 hours to save requests)
        start_time = datetime.now() - timedelta(hours=12)
    
    # Authenticate with Twitter
    client = authenticate_twitter()
    
    # Test basic API connectivity first
    try:
        # Test with a public endpoint that works with App-Only auth
        test_user = client.get_user(username="twitter")
        if test_user and test_user.data:
            print(f"Connected to Twitter API successfully! Found user: {test_user.data.username}")
            print(f"API tier: Basic (75K requests/month)")
        else:
            print("API connection seems to work but returned empty data.")
            print("This could mean the API is functioning but with limited access.")
            # Try a different test
            response = client.search_recent_tweets(query="twitter", max_results=10)
            if response and response.data:
                print(f"Search API test succeeded! Found {len(response.data)} tweets about 'twitter'")
            else:
                print("Search API also returned empty results. You may have limited access.")
    except Exception as e:
        print(f"Basic API connection test failed: {e}")
        import traceback
        trace
    
    # Split exchanges into groups to avoid complex queries that might fail
    # and to better control request volume
    exchange_groups = [
        ["coinbase", "binance", "kraken"], 
        ["kucoin", "huobi", "okx", "bybit"],
        ["hyperliquid", "uniswap", "sushiswap", "pancakeswap"], 
        ["curve", "balancer", "dydx", "gmx", "1inch", "jupiter", "raydium"]
    ]
    
    all_tweets = []
    requests_used = 0
    
    for group in exchange_groups:
        print(f"Searching for tweets mentioning: {', '.join(group)}...")
        exchange_query = " OR ".join([f"\"{exchange}\"" for exchange in group])
        query = f"({exchange_query}) -is:retweet" # Added has:cashtags to focus on relevant tweets
        
        # Basic tier limitations - use smaller page size and fewer pages
        group_tweets = search_twitter(client, query, start_time=start_time, max_results=25)
        requests_used += 1  # Each search counts as at least one request
        
        if group_tweets:
            all_tweets.extend(group_tweets)
            print(f"  Found {len(group_tweets)} tweets for this group")
        else:
            print(f"  No tweets found for this group")
            
    print(f"Total tweets collected: {len(all_tweets)}")
    print(f"Estimated API requests used: {requests_used}")
    
    # Process all collected tweets
    tweets = all_tweets
    
    # Process tweets
    new_findings = []
    unique_cashtags = set()  # Track unique cashtags for AI analysis
    
    for tweet in tweets:
        cashtags = extract_cashtags(tweet.text)
        if not cashtags:
            continue
            
        exchanges_mentioned = detect_exchange_mentions(tweet.text.lower())
        if not exchanges_mentioned:
            continue
            
        for cashtag in cashtags:
            ticker = cashtag.upper()
            
            # Skip if we've seen this cashtag recently
            if ticker in history["seen_cashtags"]:
                last_seen = datetime.fromisoformat(history["seen_cashtags"][ticker]["last_seen"])
                # Only consider it new if we haven't seen it in the last 7 days
                if (datetime.now() - last_seen).days < 7:
                    continue
            
            # Track unique cashtags
            unique_cashtags.add(ticker)
            
            # Add to new findings
            new_findings.append({
                "ticker": ticker,
                "tweet_text": tweet.text,
                "tweet_id": tweet.id,
                "tweet_url": f"https://twitter.com/user/status/{tweet.id}",
                "created_at": tweet.created_at.isoformat(),
                "exchanges_mentioned": ", ".join(exchanges_mentioned),
                "likes": tweet.public_metrics["like_count"] if hasattr(tweet, "public_metrics") else 0,
                "retweets": tweet.public_metrics["retweet_count"] if hasattr(tweet, "public_metrics") else 0
            })
            
            # Update history
            history["seen_cashtags"][ticker] = {
                "last_seen": datetime.now().isoformat(),
                "exchanges": list(set(exchanges_mentioned + 
                                     history["seen_cashtags"].get(ticker, {}).get("exchanges", [])))
            }
    
    # Update last check time
    history["last_check"] = datetime.now().isoformat()
    save_cashtag_history(history)
    
    # Save results
    if new_findings:
        # Create or append to CSV
        try:
            existing_df = pd.read_csv(RESULTS_FILE)
            new_df = pd.DataFrame(new_findings)
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            combined_df.to_csv(RESULTS_FILE, index=False)
        except (FileNotFoundError, pd.errors.EmptyDataError):
            pd.DataFrame(new_findings).to_csv(RESULTS_FILE, index=False)
            
        print(f"Found {len(new_findings)} new cashtags with exchange mentions")
        for finding in new_findings:
            print(f"${finding['ticker']} - Mentioned with: {finding['exchanges_mentioned']}")
            
        # AI Analysis of unique cashtags - prioritizing most mentioned tokens to conserve resources
        if CLAUDE_API_KEY != "YOUR_CLAUDE_API_KEY" and unique_cashtags:
            # Count mentions of each ticker to prioritize most discussed ones
            ticker_counts = {}
            for finding in new_findings:
                ticker = finding["ticker"]
                ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1
            
            # Sort tickers by mention count
            sorted_tickers = sorted(ticker_counts.items(), key=lambda x: x[1], reverse=True)
            prioritized_tickers = [t[0] for t in sorted_tickers[:5]]  # Analyze top 5 only to conserve API usage
            
            if len(prioritized_tickers) > 0:
                print(f"Performing AI analysis on top {len(prioritized_tickers)} cashtags (of {len(unique_cashtags)} total)...")
                
                ai_analyses = []
                
                # Process each prioritized cashtag
                for ticker in prioritized_tickers:
                    print(f"Analyzing ${ticker} (mentioned {ticker_counts[ticker]} times)...")
                    
                    # Get all exchanges mentioned with this ticker
                    ticker_findings = [f for f in new_findings if f["ticker"] == ticker]
                    all_exchanges = set()
                    for finding in ticker_findings:
                        exchanges = finding["exchanges_mentioned"].split(", ")
                        all_exchanges.update(exchanges)
                    
                    # Check if we already have enough tweets before making additional API calls
                    if len(ticker_findings) >= 5:
                        print(f"  Using {len(ticker_findings)} existing tweets (saving API requests)")
                        additional_tweets = []
                    else:
                        # Collect a limited number of additional tweets about this cashtag
                        target_count = min(10, ANALYSIS_TWEET_COUNT)  # Reduced from 20 to 10 to save API requests
                        print(f"  Collecting up to {target_count} additional tweets...")
                        additional_tweets = collect_additional_tweets(client, ticker, target_count)
                    
                    # If we have enough tweets, perform AI analysis
                    all_tweet_texts = [f["tweet_text"] for f in ticker_findings]
                    if additional_tweets:
                        all_tweet_texts.extend(additional_tweets)
                    
                    if len(all_tweet_texts) >= 3:  # Analyze if we have at least 3 tweets
                        all_tweet_texts = all_tweet_texts[:15]  # Limit to 15 tweets max to save Claude API tokens
                        
                        # Analyze with Claude
                        analysis = analyze_with_claude(ticker, all_tweet_texts, list(all_exchanges))
                        
                        # Add ticker and basic info to analysis
                        analysis["ticker"] = ticker
                        analysis["exchanges"] = ", ".join(all_exchanges)
                        analysis["tweet_count"] = len(all_tweet_texts)
                        analysis["analysis_time"] = datetime.now().isoformat()
                        analysis["mention_count"] = ticker_counts[ticker]
                        
                        # Convert lists to strings for CSV storage
                        if "key_points" in analysis and analysis["key_points"]:
                            analysis["key_points"] = "• " + "\n• ".join(analysis["key_points"])
                        
                        if "red_flags" in analysis and analysis["red_flags"]:
                            analysis["red_flags"] = "• " + "\n• ".join(analysis["red_flags"])
                        
                        ai_analyses.append(analysis)
                        
                        print(f"  Analysis complete:")
                        print(f"    - Sentiment: {analysis.get('sentiment_score', 'N/A')}")
                        print(f"    - Recommendation: {analysis.get('recommendation', 'N/A')}")
                        
                        # Add short cooldown between Claude API calls
                        if ticker != prioritized_tickers[-1]:
                            print("  Pausing briefly between analyses...")
                            time.sleep(2)
                    else:
                        print(f"  Not enough tweets collected for ${ticker} analysis")
                
                # Save AI analyses
                if ai_analyses:
                    try:
                        existing_analyses_df = pd.read_csv(ANALYSIS_FILE)
                        new_analyses_df = pd.DataFrame(ai_analyses)
                        combined_analyses_df = pd.concat([existing_analyses_df, new_analyses_df], ignore_index=True)
                        combined_analyses_df.to_csv(ANALYSIS_FILE, index=False)
                    except (FileNotFoundError, pd.errors.EmptyDataError):
                        pd.DataFrame(ai_analyses).to_csv(ANALYSIS_FILE, index=False)
                        
                    print(f"Saved {len(ai_analyses)} AI analyses to {ANALYSIS_FILE}")
            else:
                print("No tickers to analyze after prioritization")
        else:
            if CLAUDE_API_KEY == "your_claude_api_key_here":
                print("AI analysis skipped: Claude API key not configured")
            else:
                print("AI analysis skipped: No unique cashtags found")
    else:
        print("No new cashtags found")

if __name__ == "__main__":
    main()