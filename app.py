import openai
import requests
from bs4 import BeautifulSoup
import json
import time
import os
import io
import tempfile
import logging
import pandas as pd
from serpapi import GoogleSearch
from urllib.parse import urljoin
from urllib.parse import urlparse
from newspaper import Article
import re
import streamlit as st

# Securely load API keys
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
SERP_API_KEY = st.secrets["SERP_API_KEY"]

# Initialize OpenAI client
client = openai.Client(api_key=OPENAI_API_KEY)

def get_citations(article_response):
    article_message_id = article_response.data[0].id
    article_message_content = article_response.data[0].content[0].text
    article_message_role= article_response.data[0].role
    article_message_file_id = article_response.data[0].file_ids
    print(f"Article Message Id: {article_message_id}")
    print(f"Article Message Content: {article_message_content}")
    print(f"Article Message Role: {article_message_role}")
    print(f"Article Message File Id: {article_response.data[0].file_ids}")
    annotations = article_message_content.annotations
    print(annotations)
    citations = []
    print(f"File ID in Citation:{article_message_file_id}")

    # Iterate over the annotations and add footnotes
    for index, annotation in enumerate(annotations):
        # Replace the text with a footnote
        article_message_content.value = article_message_content.value.replace(annotation.text, f' [{index}]')

        # Gather citations based on annotation attributes
        if (file_citation := getattr(annotation, 'file_citation', None)):
            cited_file = client.files.retrieve(file_citation.file_id)
            citations.append(f'[{index}] from {cited_file.filename}')
        elif (file_path := getattr(annotation, 'file_path', None)):
            cited_file = client.files.retrieve(file_path.file_id)
            citations.append(f'[{index}] Click <here> to download {cited_file.filename}')
            # Note: File download functionality not implemented above for brevity

    # Add footnotes to the end of the message before displaying to user
    article_message_content.value += '\n' + '\n'.join(citations)
    return article_message_content.value

def get_root_domain(url):
    """ Extract the root domain from a URL """
    parsed_url = urlparse(url)
    # The root domain is combination of netloc's domain and suffix
    domain_parts = parsed_url.netloc.split('.')
    root_domain = '.'.join(domain_parts[-2:]) if len(domain_parts) > 1 else parsed_url.netloc
    return root_domain

def scrape_articles(query):
    all_results = []

    for i in range(2):  # Iterating over three pages

        start_index = i * 10  # Google usually shows 10 results per page
        params = {
            "engine": "google",
            "q": query,
            "start": start_index,
            "gl": "us",  # Country setting
            "api_key": SERP_API_KEY  # Replace with your SERP API key
        }
        search = GoogleSearch(params)  # Use the GoogleSearch class
        results = search.get_dict()
        serp_data = results["organic_results"]

        for result in serp_data:
            try:
                link = result['link']
                title = result['title']
                snippet = result.get('snippet', '')

                article_text = Article(link)
                article_text.download()
                article_text.parse()

                text = article_text.text
                authors = article_text.authors
                publish_date = article_text.publish_date

                # Check if the article text is 500 words or longer
                if len(text.split()) >= 500:
                    root_domain = get_root_domain(link)  # Ensure this function is defined
                    all_results.append((root_domain, link, title, authors, publish_date, snippet, text))
            except Exception as e:
                print(f"Couldn't download article from {link}: {e}")

    # Define the column names for the DataFrame
    columns = ['Root Domain', 'Link', 'Title', 'Authors', 'Publish Date', 'Snippet', 'Text']

    # Create the DataFrame with column names
    article_df = pd.DataFrame(all_results, columns=columns)

    # Save to CSV
    article_df.to_csv("articles_dataframe.csv", index=False)
    print("Filtered Results from SerpAPI")

    return article_df  # Return the DataFrame of search results



def upload_article(content, article_index,title):
    file_path = f"{title}.txt"

    # Check if the content is too large
    if len(content.encode('utf-8')) > 10_000_000:  # 1MB in bytes
        print(f"Article {article_index} size exceeds 1MB, skipping upload.")
        return None

    try:
        # Write content to a text file
        with open(file_path, 'w', encoding='utf-8') as file:
            file.write(content)

        # Upload the file
        with open(file_path, 'rb') as f:
            response = client.files.create(file=f, purpose='assistants')
            file_id = response.id
            print(f"Uploaded article {article_index} with file ID: {file_id}")
            return file_id
    except Exception as e:
        print(f"Error uploading article {article_index}: {e}")
        return None
    #finally:
        # Delete the local file
        #if os.path.exists(file_path):
            #os.remove(file_path)


def sanitize_url(url):
    """Sanitize the URL to make it suitable for use in a filename."""
    sanitized = re.sub(r'[^\w\-_\. ]', '_', url)  # Replace non-alphanumeric characters with '_'
    return sanitized

def analyze_articles(thread_id, file_ids,query,status):
    notes = []
    individual_file_ids = []  # List to store individual file IDs
    uploaded_notes_ids = []
    for file_id, link in file_ids:
        if file_id is None:
            continue

        sanitized_link = sanitize_url(link)

        # Create an Assistant with retrieval for analyzing articles
        assistant_id = client.beta.assistants.create(
            instructions=f"""You are an all-knowing expert AI researcher information extractor. You are compiling as much useful information and as many facts as you can for an article you are writing about {query}
            You always cite your sources by referencing the URL where the info was found as the source.
            Analyze articles and provide insights using the charting method.Write at least 6000 words.
            Be exceptionally detailed, thorough, are extremely well organized and hierarchical in your organization.
            ALWAYS include all facts, data, and information.
            Write with markdown.

            The format of your notes should be:
            Article Source Title: You will fill this in with The Title of the Article You are extracting info from.
            Article Source URL for Later Citation: You will fill this in with The URL of the Article. This must be the http or https url found in the corpus.
            Extracted Information: You will fill this in with Your highly comprehensive and detailed extractin. It should cover everything found in the file.
            It should have at the very least 10 sections (categories per the template), and under each section you should have many subsections and subsubsections. here is a rough template for note taking:
            When extracting info DO NOT say something like -the article talks about- instead, give the actual fact or information from the article.
            Maximum specific information extraction is the ultimate goal here.
            Never generically discribe the corpus like -The corpus contains lots of information about the topic-, instead you MUST actually extract data,information,facts,sources,etc. Not just describe it generically.
            You are extracting info found in the article itself.

            Follow this template for your informatione extraction:


            General Template (Yours will be far more extensive)
            ####
            Topic/Subject: [Main Topic or Subject Name]
            Article Title: The title of the article you are referencing.
            Source: The URL of the website you are referencing. Always starts with http or https
            Authors: If there are any authors found, include the author name and bio here
            Date: If the article is dated, include the date found here.

            Category 1: [Name of the First Main Category]
            Subcategory 1.1: [Name of Subcategory]
            Fact/Info 1: [Detail or point of information] - Source: [Source 1]
            Fact/Info 2: [Detail or point of information] - Source: [Source 2]
            Fact/Info 3: [Detail or point of information] - Source: [Source 3]
            Fact/Info 4: [Detail or point of information] - Source: [Source 4]
            Fact/Info 5: [Detail or point of information] - Source: [Source 5]
            Fact/Info 6: [Detail or point of information] - Source: [Source 6]
            (as many facts as you can include, up to 20)
            ...

            Subcategory 1.2: [Name of Subcategory]
            Fact/Info 1: [Detail or point of information] - Source: [Source 1]
            Fact/Info 2: [Detail or point of information] - Source: [Source 2]
            Fact/Info 3: [Detail or point of information] - Source: [Source 3]
            Fact/Info 4: [Detail or point of information] - Source: [Source 4]
            Fact/Info 5: [Detail or point of information] - Source: [Source 5]
            Fact/Info 6: [Detail or point of information] - Source: [Source 6]
            (as many facts as you can include, up to 20)
            ...
            Sub-Subcategory 1.2.1: [Name of Sub-Subcategory]
            Fact/Info 1: [Detail or point of information] - Source: [Source 1]
            Fact/Info 2: [Detail or point of information] - Source: [Source 2]
            Fact/Info 3: [Detail or point of information] - Source: [Source 3]
            Fact/Info 4: [Detail or point of information] - Source: [Source 4]
            Fact/Info 5: [Detail or point of information] - Source: [Source 5]
            Fact/Info 6: [Detail or point of information] - Source: [Source 6]
            (as many facts as you can include, up to 20)


            Category 2: [Name of the Second Main Category]
            Subcategory 2.1: [Name of Subcategory]
            Fact/Info 1: [Detail or point of information] - Source: [Source 1]
            Fact/Info 2: [Detail or point of information] - Source: [Source 2]
            Fact/Info 3: [Detail or point of information] - Source: [Source 3]
            Fact/Info 4: [Detail or point of information] - Source: [Source 4]
            Fact/Info 5: [Detail or point of information] - Source: [Source 5]
            Fact/Info 6: [Detail or point of information] - Source: [Source 6]
            (as many facts as you can include, up to 20)
            ...

            Category 3: [Name of the Third Main Category]
            Subcategory 3.1: [Name of Subcategory]
            Fact/Info 1: [Detail or point of information] - Source: [Source 1]
            Fact/Info 2: [Detail or point of information] - Source: [Source 2]
            Fact/Info 3: [Detail or point of information] - Source: [Source 3]
            Fact/Info 4: [Detail or point of information] - Source: [Source 4]
            Fact/Info 5: [Detail or point of information] - Source: [Source 5]
            Fact/Info 6: [Detail or point of information] - Source: [Source 6]
            (as many facts as you can include, up to 20))
            ...
            Deep Insights and Analysis:
            Insight 1: [In-depth analysis or interpretation of the collected facts]
            Insight 2: [In-depth analysis or interpretation of the collected facts]

            Generated Data Table: (For any data you find, either in tabular form, or from within context that can be compiled into a table, provide it below.)
            Data Table 1: use csv format
            Data Table 2: use csv format
            Data Table 3: use csv format
            ...

            ####





            You MUST write at least 6,000 words total.
            Please take a deep breath, think step by step, and then begin.
            Never say anything like -i will now begin taking notes-, just start taking notes. You should always start your response with:
            Article Source Title: You will fill this in with The Title of the Article You are extracting info from.
            Article Source URL for Later Citation: You will fill this in with The URL of the Article.This must be the http or https url found in the corpus.
            etc.
            Stop and print the entire set of notes when you are satisfied you have fully extracted all relevant info for the query.""",
            model="gpt-3.5-turbo-1106",
            tools=[{"type": "retrieval"}]
        ).id

        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=f"""Please analyze the file with ID {file_id} and extract ALL possible salient facts and information.
                      At the beginning always start with:
                      ###
                      Topic/Subject: [Main Topic or Subject Name]
                      Article Title: The title of the article you are referencing.
                      Source: The URL of the website you are referencing. Always starts with http or https
                      Authors: If there are any authors found, include the author name and bio here
                      Date: If the article is dated, include the date found here.
                      ###
                      The total length of the information extracted should be at least 6000 words long. Always include at least one generated data table, even if it is simple.
                      Never say anything like -i will now begin taking notes-, just start taking notes. You should always start your response with:
                      Article Source Title: You will fill this in with The Title of the Article You are extracting info from.
                      Article Source URL for Later Citation: You will fill this in with The URL of the Article. This must be the http or https url found in the corpus.
                      etc.
                      Full Notes:""",
            file_ids=[file_id]
        )
        print(f"Created message for file ID {file_id} in thread {thread_id}")

        run_response = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=assistant_id
        )
        print(f"Run created with ID: {run_response.id}")

        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_response.id).status
            if run_status in ['queued', 'in_progress']:
                time.sleep(5)  # Wait for 5 seconds before polling again
                print(run_status)
                continue
            if run_status in ['completed', 'failed']:
                print(run_status)
                break
            elif run_status == 'requires_action':
                print(run_status)
                break

        response = client.beta.threads.messages.list(thread_id=thread_id)

        individual_outline_file_path = f'final_outline_{sanitized_link}.txt'
        if len(response.data) > 0 and response.data[0].role == "assistant":
            article_message_content = response.data[0].content[0].text.value
            #article_message_content2 = response.data[1].content[0].text.value
            #article_message_content3 = response.data[2].content[0].text.value
            #print(f"Older Still Part of Notes:{article_message_content3}")
            #print(f"Older Part of Notes:{article_message_content2}")
            # Count the number of words in the article message content
            word_count = len(article_message_content.split())
            print(word_count)
            status.text(article_message_content)

            # Check if the word count is 500 or more
            if word_count >= 300:
                # Add to notes if the condition is met
                notes.append({"file_id": file_id, "note": article_message_content})

                # Write the content to a file
                with open(individual_outline_file_path, 'w', encoding='utf-8') as file:
                    file.write(article_message_content)

                # Upload the file and store the individual file ID
                with open(individual_outline_file_path, 'rb') as file:
                    response = client.files.create(file=file, purpose='assistants')
                    individual_file_id = response.id
                    individual_file_ids.append(individual_file_id)
            else:
                print(f"Skipping file ID {file_id} as the note is less than 500 words.")

    print(f"Individual File Ids:{individual_file_ids}")

    # Create and save a DataFrame from the notes
    df_notes = pd.DataFrame(notes)
    notes_file_path = "aggregated_notes.csv"
    df_notes.to_csv(notes_file_path, sep='\t', index=False)



    return notes_file_path, individual_file_ids, df_notes


import streamlit as st
import pandas as pd
import time
import openai
# Import other necessary libraries and functions

# Assuming your functions (scrape_articles, upload_article, analyze_articles, etc.) are defined here...

def main():
    st.title("Research and Outline Generation Tool")
    query = st.text_input("Enter your query", "2023 Israel Hamas War Timeline")
    # Uploading articles

    if st.button("Start Research"):
        outline = []
        i = 0
        file_ids_attempt = []
        progress = st.progress(0)
        status = st.empty()

        # Scraping articles
        status.text('Scraping articles...')
        articles = scrape_articles(query)
        status.text('Articles scraped successfully!')
        progress.progress(10)



        for index, row in articles.iterrows():
            article = {
                    'Root Domain': row['Root Domain'],
                    'Link': row['Link'],
                    'Title': row['Title'],
                    'Authors': row['Authors'],
                    'Publish Date': row['Publish Date'],
                    'Snippet': row['Snippet'],
                    'Text': row['Text']
                }
            status.text(f'Uploading article {index + 1} of {len(articles)}...')
            article_content = '\n'.join(f'{key}: {value}' for key, value in row.items())
            file_id = upload_article(article_content, index, row['Title'])
            file_ids_attempt.append((file_id, row['Title']))
        status.text('All articles uploaded successfully!')
        progress.progress(30)

        # Analyzing articles
        file_ids = [(str(file_id), link) for file_id, link in file_ids_attempt if file_id is not None and isinstance(file_id, str)]
        status.text('Analyzing articles...')
        thread_id = client.beta.threads.create().id
        back_from_analyze = analyze_articles(thread_id, file_ids,query,status)
        aggregated_notes_file_path = back_from_analyze[0]
        status.text(back_from_analyze[0])
        uploaded_file_ids = back_from_analyze[1]
        full_notes = back_from_analyze[2]
        status.text('Analysis completed!')
        progress.progress(60)

        # Finalizing outline
        with open(aggregated_notes_file_path, 'rb') as f:
            try:
                notes_file_response = client.files.create(file=f, purpose='assistants')
                notes_file_id = notes_file_response.id
                st.text(f)
            except Exception as e:
                st.error(f"Failed to upload file: {e}")
                return

        outline_assistant_id = client.beta.assistants.create(
            instructions=f"Please simulate an expert on writing comprehensive long-form article outlines on the topic of {query}."
            "As a superhuman AI, you do this job better than any human in terms of information gain."
            "Based on the files provided in the reference corpuses, please improve, expand and extend the article outline with each new round."
            f"The reference files have the following file ids: {notes_file_id}. You DO have access to these files, even if you assume you dont."
            "Make sure to double check, the file is available. Use the notes corpus to make sure you are not missing anything.Write at least 6000 words."
            "Write your extremely detailed outline in markdown with deep hierarchies."
            "The outline should include all unique information found in the corpus, highly organized, retaining all salient facts. The primary goal of this outline is maximum information density.6,000 word MINIMUM."
            "Say research complete when done.",
            model="gpt-3.5-turbo-1106",
            tools=[{"type": "retrieval"}]
        ).id

        outline_thread_id = client.beta.threads.create().id

        prompt = "Please create an initial outline based on the aggregated notes."
        client.beta.threads.messages.create(
            thread_id=outline_thread_id,
            role="user",
            content=prompt,
            file_ids=[notes_file_id]
        )

        run_response = client.beta.threads.runs.create(
            thread_id=outline_thread_id,
            assistant_id=outline_assistant_id
        )

        while i < 3:
            run_status = client.beta.threads.runs.retrieve(thread_id=outline_thread_id, run_id=run_response.id).status
            if run_status in ['queued', 'in_progress']:
                time.sleep(5)  # Wait for 5 seconds before polling again
                print(run_status)
                continue
            if run_status in ['completed', 'failed']:
                break
            elif run_status == 'requires_action':
                break

            response = client.beta.threads.messages.list(thread_id=outline_thread_id)
            the_outline = response.data[-1].content[0].text
            prompt = f"Please significantly extend and improve the outline using the notes found in file ids: {notes_file_id} for the goal of the query: {query}."
            "For each top level section, list the urls of the sources that apply to that section from the notes corpus like this: [Relevant Source from Notes: https://the url found in the notes]"
            "You DO have access to these files, even if you assume you dont. Make sure you look at all the files when creating and improving your outline."
            "Make sure to double check, the file is available. Use the notes corpus to make sure you are not missing anything.The goal is to add all missing facts, data, stats, main points, missing sections, missing subsections, etc."
            f"Here is the outline to extend and improve using the corpus: {the_outline.value}"

            client.beta.threads.messages.create(
                thread_id=outline_thread_id,
                role="user",
                content=prompt,
                file_ids=[notes_file_id]
            )

            run_response = client.beta.threads.runs.create(
                thread_id=outline_thread_id,
                assistant_id=outline_assistant_id
            )
            print(f"Outline Run created with ID: {run_response.id}")
            print(f"Created message for file ID {notes_file_id} in thread {outline_thread_id}")
            
            while True:
                run_status = client.beta.threads.runs.retrieve(thread_id=outline_thread_id, run_id=run_response.id).status
                if run_status in ['queued', 'in_progress']:
                    run_status = client.beta.threads.runs.retrieve(thread_id=outline_thread_id, run_id=run_response.id).status
            
                    time.sleep(5)  # Wait for 5 seconds before polling again
                    print(run_status)
                    continue
                if run_status in ['completed', 'failed']:
                    run_status = client.beta.threads.runs.retrieve(thread_id=outline_thread_id, run_id=run_response.id).status

                    print(run_status)
                    print("run status outline loop")
                    break
                elif run_status == 'requires_action':
                    print(run_status)
                    break
            
            # Retrieve the assistant's response
            response = client.beta.threads.messages.list(thread_id=outline_thread_id)
            print(response.data)
            article_message_id = response.data[0].id
            article_message_content = response.data[0].content[0].text
            article_message_role= response.data[0].role
            article_message_file_id = response.data[0].file_ids
        
            if article_message_role == "assistant":
                #message_citations = get_citations(response)
                outline.append(article_message_content.value)
    
            i += 1

        status.text('Finalizing outline...')
        outline_file_path = "all_outlines.csv"
        
        @st.cache
        def convert_df(df):
            # IMPORTANT: Cache the conversion to prevent computation on every rerun
            return df.to_csv().encode('utf-8')
            
        df_outline = pd.DataFrame(outline)
        final_outline_file_path = 'final_outline.txt'
            
        status.text('Outline generation concluded.')
        st.text(outline)
        progress.progress(100)
        

        
        aggregate_notes_csv = convert_df(full_notes)
        st.download_button(
            label="Download Aggregated Notes",
            data=aggregate_notes_csv,
            file_name=aggregated_notes_file_path,
            mime='text/csv',
        )

        all_outlines_csv = convert_df(df_outline)
        st.download_button(
            label="Download All Outlines",
            data=all_outlines,
            file_name=aggregated_notes_file_path,
            mime='text/csv',
        )

        st.download_button('Download Final Article', outline[-1])



    
        st.success("Research and outline generation completed successfully.")

if __name__ == "__main__":
    main()
