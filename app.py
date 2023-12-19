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
import zipfile
import concurrent.futures
import concurrent.futures

# Securely load API keys
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
SERP_API_KEY = st.secrets["SERP_API_KEY"]
# Define your Typeform API token and endpoint
api_token =  st.secrets["TYPEFORM_API_KEY"]

# Initialize OpenAI client
client = openai.Client(api_key=OPENAI_API_KEY)

def get_citations(article_response):
    article_message_id = article_response.data[0].id
    article_message_content = article_response.data[0].content[0].text
    article_message_role= article_response.data[0].role
    article_message_file_id = article_response.data[0].file_ids
    #print(f"Article Message Id: {article_message_id}")
    #print(f"Article Message Content: {article_message_content}")
    #print(f"Article Message Role: {article_message_role}")
    #print(f"Article Message File Id: {article_response.data[0].file_ids}")
    annotations = article_message_content.annotations
    #print(annotations)
    citations = []
    #print(f"File ID in Citation:{article_message_file_id}")

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

    for i in range(1):  # Iterating over three pages

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

def sanitize_url(url):
    # Implement your URL sanitization logic here
    return url

def worker(file_id_link_tuple, query,status,client):
    file_id, link = file_id_link_tuple
    if file_id is None:
        return None

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
    thread_id = client.beta.threads.create().id
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
            time.sleep(5)
            continue
        if run_status in ['completed', 'failed', 'requires_action']:
            break

    response = client.beta.threads.messages.list(thread_id=thread_id)
    if len(response.data) > 0 and response.data[0].role == "assistant":
        article_message_content = response.data[0].content[0].text.value
        word_count = len(article_message_content.split())

        if word_count >= 300:
            note_file_path = f'final_outline_{sanitized_link}.txt'
            with open(note_file_path, 'w', encoding='utf-8') as file: 
                file.write(article_message_content)

            with open(note_file_path, 'rb') as file:
                response = client.files.create(file=file, purpose='assistants')
                individual_file_id = response.id

            return {"file_id": file_id, "note": article_message_content, "individual_file_id": individual_file_id}
            
    return None
    
def analyze_articles(file_ids, query, status, client):
    notes = []
    individual_file_ids = []

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(worker,file_id_link_tuple, query, status, client) for file_id_link_tuple in file_ids]

        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result is not None:
                notes.append(result["note"])
                individual_file_ids.append(result["individual_file_id"])

    df_notes = pd.DataFrame({'Note': notes})

    return df_notes, individual_file_ids, df_notes
    
def convert_df_to_csv_bytes(df):
    # Convert DataFrame to CSV and encode to bytes
    return df.to_csv(index=False).encode('utf-8')

def save_string_to_file(string_data, file_name):
    # Save a string to a file
    with open(file_name, 'w') as file:
        file.write(string_data)

def save_bytes_to_file(bytes_data, file_name):
    # Save bytes data to a file
    with open(file_name, 'wb') as file:
        file.write(bytes_data)    

def fix_markdown(text):
    # Replace \\n with proper line breaks (\n)
    text = re.sub(r'\\n', '\n', text)

    # Remove extra newlines at the beginning and end of paragraphs
    text = re.sub(r'\n\n+', '\n\n', text)

    # Split sections using ", " and format them
    sections = re.split(r'", "', text)
    formatted_sections = []
    for section in sections:
        formatted_section = re.sub(r'\n\n', '\n', section.strip())
        formatted_sections.append(formatted_section)

    return '\n\n'.join(formatted_sections)


def remove_sections_within_brackets(text):
    # Define a regular expression pattern to match text within square brackets
    pattern = r'\[Next Section to Write:[^\]]+\]'

    # Use re.sub to replace all matches with an empty string
    cleaned_text = re.sub(pattern, '', text)

    return cleaned_text

def query_assistant(prompt):
    response = client.chat.completions.create(
        model="gpt-4-1106-preview",
        messages=[
        {"role": "system", "content": f"You are an award winning NYTimes writer that iteratively writes articles based on your outline and notes. If you are given a specific section to work on, please only do that section. When all sections are complete return - Article Complete -."},
        {"role": "user", "content": prompt}
        ],
        max_tokens=4000
    )
    return response.choices[0].message.content



def main():

    st.title("Automated Content Creation Pipeline - Information Gain")
    query = st.text_input("Enter your query", "2023 Israel Hamas War Timeline")
    outline = []
    final_article = []
    conversation = []
    i = 0

            
    if st.button("Start Research"):

        
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
        back_from_analyze = analyze_articles(file_ids,query,status,client)
        aggregated_notes_file_path = back_from_analyze[0]
        #status.text(back_from_analyze[0])
        uploaded_file_ids = back_from_analyze[1]
        full_notes = back_from_analyze[2]
        status.text('Analysis completed!')
        progress.progress(60)
        

        outline_assistant_id = client.beta.assistants.create(
            instructions=f"Please simulate an expert on writing comprehensive long-form article outlines on the topic of {query}."
            "As a superhuman AI, you do this job better than any human in terms of information gain."
            "Based on the files provided in the reference corpuses, please improve, expand and extend the article outline with each new round."
            f"The reference files have the following file ids: {uploaded_file_ids}. You DO have access to these files, even if you assume you dont."
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
            file_ids=uploaded_file_ids
        )

        run_response = client.beta.threads.runs.create(
            thread_id=outline_thread_id,
            assistant_id=outline_assistant_id
        )


        
        run_status = client.beta.threads.runs.retrieve(thread_id=outline_thread_id, run_id=run_response.id).status
        while True:
            if run_status in ['queued', 'in_progress']:
                run_status = client.beta.threads.runs.retrieve(thread_id=outline_thread_id, run_id=run_response.id).status
        
                time.sleep(1)  # Wait for 5 seconds before polling again
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

        response = client.beta.threads.messages.list(thread_id=outline_thread_id)
        the_outline = response.data[0].content[0].text
        #st.write(response.data)
        #st.write(the_outline.value)
        prompt = f"""Please significantly extend and improve the outline using the notes found in file ids: {uploaded_file_ids} for the goal of the query: {query}.
        For each top level section, list the urls of the sources that apply to that section from the notes corpus like this: [Relevant Source from Notes: https://the url found in the notes]
        You DO have access to these files, even if you assume you dont. Make sure you look at all the files when creating and improving your outline.
        Make sure to double check, the file is available. Use the notes corpus to make sure you are not missing anything.The goal is to add all missing facts, data, stats, main points, missing sections, missing subsections, etc.
        Here is the outline to extend and improve using the corpus: {the_outline.value}"""

        client.beta.threads.messages.create(
            thread_id=outline_thread_id,
            role="user",
            content=prompt,
            file_ids=uploaded_file_ids
        )

        run_response = client.beta.threads.runs.create(
            thread_id=outline_thread_id,
            assistant_id=outline_assistant_id
        )
        print(f"Outline Run created with ID: {run_response.id}")
        print(f"Created message for file ID {uploaded_file_ids} in thread {outline_thread_id}")
        
        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=outline_thread_id, run_id=run_response.id).status
            if run_status in ['queued', 'in_progress']:
                run_status = client.beta.threads.runs.retrieve(thread_id=outline_thread_id, run_id=run_response.id).status
        
                time.sleep(1)  # Wait for 5 seconds before polling again
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
        outline_message_id = response.data[0].id
        outline_message_content = response.data[0].content[0].text
        outline_message_role= response.data[0].role
        outline_message_file_id = response.data[0].file_ids
    
        outline.append(outline_message_content)

        status.text('Finalizing outline...')
        df_outline = pd.DataFrame(outline)

        
        status.text('Outline generation concluded. Now Writing Full Article.')
        progress.progress(70)
        
        # Convert DataFrames to CSV bytes
        aggregate_notes_csv_bytes = convert_df_to_csv_bytes(full_notes)
        all_outlines_csv_bytes = convert_df_to_csv_bytes(df_outline)

        outline = str(outline)
        notes = full_notes

        prompt = f"""You will be writing a long-form article based on an outline and a notes corpus. You include everything in the outline, including the top level sections, subsections, and sub-subsections. Start by writing a table of contents (only included in first iteration) that mirrors exactly what you see in the outline with all sections/subsections/subsubsections included. Enhance if needed. It should have many sections, subsections, and subsubsections. Then go section by section, the table of contents should only be returned once. While following the outline, draw extensively on your notes corpus. The notes contains many sections, each related to a specific source. The sections of notes on individual sources are seperated by file-Gcjd8AsDYc1zhct03uHXyoqo filenames like that.  When citing a source, always reference a specific url from the notes corpus. The citation should be inline and use the format: [URL Title from the notes,URL from the notes always starting with http or https]. After writing a section, always provide the next section title that needs to be written like this [Next Section to Write: Next Section Title].
        Here is the outline you will follow: #### {outline} ####. Here is the notes corpus to leverage to write as complete and comprehensive an article as possible. Notes: #### {notes} #### .You never write generically or with generalizations, you always attempt to use specific facts, data, etc. You also like to include markdown tables. Make sure to cite your sources inline. Each section is at least 2000 words. You write in beautiful markdown and always cite your sources from http or https urls found in your notes corpus. Leverage your notes to the fullest extent possible. At the beginning of each section, make a detailed recommendation for an image to include. This image should be a simplistic representation of the given section. It should NEVER include text or be super complex. The image description should be very specific to help a generative AI render it accurately. Provide these instructions like this: [Insert Image Here: The Image Description] . Also Please include markdown tables from data found in the notes where you think the table will add value and ease of reading for the reader. Each section will likely have a table or other structured markdown viz. Be extremely thorough and comprehensive with a focus on making the article as useful and actionable as possible. When referencing a url, do it inline and use [URL Title from the notes,URL from the notes always starting with http or https]. Never cite references like this [[1†source]]. Always use the actual http or https url. Try to use as many different sources as possible in your article. Because the notes are so extensive, you should be referencing sources, all sources should be referenced by the end of the article. When you have finished all the sections return the text - Article Complete - Start with the table of contents, and then write each section of the table of contents one at a time."""
        
        conversation.append(str(prompt))
        
        conversation.append("Table of Contents:")
        conversation.append("---------------------------------------")
        query_gpt = query_assistant(str(conversation))
        st.write(query_gpt)
        conversation.append(query_gpt)
        final_article.append(query_gpt)
        i=1
        while "Article Complete" not in final_article:
          progress.progress(70 + 1)
          status.text(f'Writing Article Section {i}')
          
          st.write("-----------------------------")
          keep_going = "Please write the next specified section last specified. If all sections have been completed, return the text  - Article Complete - when finished with all sections. Next Section:"
          conversation.append(keep_going)
          st.write(conversation)
          second_query_gpt = query_assistant(str(conversation))
          conversation.append(second_query_gpt)
          st.write(second_query_gpt)
          final_article.append(second_query_gpt)
          #print(f"GPT Response:{query_gpt}")
          i+=1
        
        if "Bibliography Complete" not in final_article:
          status.text('Writing Bibliography')
          conversation.append(query_gpt)
          add_bibliography = "Now please add a nicely formatted markdown bibliography at the end. The Bibliography should refrence the http or https links as they appear in the notes corpus that are referenced in the article. Once the bibliography is done, return the string - Bibliography Complete -"
          conversation.append(add_bibliography)
          final_query_gpt = query_assistant(str(conversation))
        
          final_article.append(final_query_gpt)
        final_article = " ".join(final_article)
        final_article =  fix_markdown(final_article)
        final_article = remove_sections_within_brackets(final_article)
        
        

        
        
        status.text('Final Article Complete. Now Creating the Survey.')



        corpus= full_notes
                
        response = client.chat.completions.create(
          model="gpt-3.5-turbo-1106",
          messages=[
                {"role": "system", "content": f"You are an expert survey writer writing a survey based on an article. Create AT LEAST 20 survey questions and their possible resonses to choose, including a few open ended response options. You create this as json only. Here is the corpus: {corpus}"},
                {"role": "user", "content": """Return only valid Typeforms api request json. Use conditional logic where appropriate to improve or enhance the survey quality, consistency, or flow.
                You will always have conditional logic, so your json needs to have a logic section. Never create questions or logic with images.
                Here is the info you need to know to create an accurate json for Typeforms api.
        
        
        Here is an example of a valid json request that uses conditional logic to help you make sure you are adhering to the proper syntax and schema. Pay special attention to syntax, exact schema and capitalization. All createItems must have a location and details:
        
        ####{
            "title": "Fly Fishing in Colorado Survey",
            "settings": {
                "language": "en",
                "progress_bar": "proportion",
                "meta": { "allow_indexing": False },
                "hide_navigation": False,
                "is_public": False,
                "is_trial": False,
                "show_progress_bar": True,
                "show_typeform_branding": True,
                "are_uploads_public": False,
                "show_time_to_complete": True,
                "show_number_of_submissions": False,
                "show_cookie_consent": False,
                "show_question_number": True,
                "show_key_hint_on_choices": True,
                "autosave_progress": True,
                "free_form_navigation": False,
                "use_lead_qualification": False,
                "pro_subdomain_enabled": False
            },
            "welcome_screens": [
                {
                    "title": "Welcome to the Fly Fishing in Colorado Survey!",
                    "properties": {
                        "show_button": True,
                        "button_text": "Start"
                    }
                }
            ],
            "fields": [
                {
                    "title": "What is your email address?",
                    "ref": "email_address",
                    "type": "email",
                    "validations": { "required": False }
                },
                {
                    "title": "How often do you go fly fishing in Colorado?",
                    "ref": "fishing_frequency",
                    "type": "multiple_choice",
                    "properties": {
                        "choices": [
                            { "label": "Less than once a year" },
                            { "label": "Once a year" },
                            { "label": "2-3 times a year" },
                            { "label": "4-6 times a year" },
                            { "label": "More than 6 times a year" }
                        ]
                    },
                    "validations": { "required": False }
                },
                {
                    "title": "Do you own your fishing equipment?",
                    "ref": "own_equipment",
                    "type": "yes_no",
                    "validations": { "required": True }
                },
                {
                    "title": "What type of fish do you primarily target?",
                    "ref": "fish_target",
                    "type": "multiple_choice",
                    "properties": {
                        "choices": [
                            { "label": "Trout" },
                            { "label": "Salmon" },
                            { "label": "Bass" },
                            { "label": "Other" }
                        ]
                    },
                    "validations": { "required": True }
                },
                {
                    "title": "Describe your most memorable fishing experience.",
                    "ref": "fishing_experience",
                    "type": "long_text",
                    "validations": { "required": False }
                },
                {
                    "title": "Select your preferred fishing locations in Colorado.",
                    "ref": "fishing_locations",
                    "type": "multiple_choice",
                    "properties": {
                        "choices": [
                            {
                                "label": "Location A",
        
                            },
                            {
                                "label": "Location B",
        
                            }
                        ],
                        "allow_multiple_selection": True
                    },
                    "validations": { "required": True }
                },
                {
                    "title": "On a scale of 1 to 10, how would you rate your fishing skills?",
                    "ref": "fishing_skills",
                    "type": "opinion_scale",
                    "properties": {
                        "steps": 10,
                        "start_at_one": True
                    },
                    "validations": { "required": True }
                },
                {
                    "title": "Would you be interested in participating in a fishing tournament?",
                    "ref": "interest_tournament",
                    "type": "yes_no",
                    "validations": { "required": True }
                },
            ],
            "thankyou_screens": [
            {
                "title": "Thank you for your responses!",
                "ref": "end_of_survey",
                "properties": {
                    "show_button": False,
                    "share_icons": False
                }
            }
            ],
            "logic": [
                {
                    "type": "field",
                    "ref": "email_address",
                    "actions": [
                        {
                            "action": "jump",
                            "details": {
                                "to": {
                                    "type": "field",
                                    "value": "fishing_frequency"
                                }
                            },
                            "condition": {
                                "op": "always",
                                "vars": []
                            }
                        }
                    ]
                },
                {
                    "type": "field",
                    "ref": "fishing_frequency",
                    "actions": [
                        {
                            "action": "jump",
                            "details": {
                                "to": {
                                    "type": "field",
                                    "value": "own_equipment"
                                }
                            },
                            "condition": {
                                "op": "always",
                                "vars": []
                            }
                        }
                    ]
                },
                {
                    "type": "field",
                    "ref": "own_equipment",
                    "actions": [
                        {
                            "action": "jump",
                            "details": {
                                "to": {
                                    "type": "field",
                                    "value": "fish_target"
                                }
                            },
                            "condition": {
                                "op": "is",
                                "vars": [
                                    {
                                        "type": "field",
                                        "value": "own_equipment"
                                    },
                                    {
                                        "type": "constant",
                                        "value": True
                                    }
                                ]
                            }
                        },
                        {
                            "action": "jump",
                            "details": {
                                "to": {
                                    "type": "field",
                                    "value": "fishing_experience"
                                }
                            },
                            "condition": {
                                "op": "is_not",
                                "vars": [
                                    {
                                        "type": "field",
                                        "value": "own_equipment"
                                    },
                                    {
                                        "type": "constant",
                                        "value": True
                                    }
                                ]
                            }
                        }
                    ]
                }
        
            ]
        
        
        }
        
        ####
        
        Before providing your json answer, check the following:
        
        Check the Supported Operations and Variable Types: Ensure the operations (op) used in your logic are supported by the system.
        For example, if you are comparing a field that returns a boolean (Yes/No), use operations like is or is_not, and ensure that the constant value you compare with is also a boolean (true or false).
        
        Ensure Correct Data Types: Make sure the types of the constants (constant) in your conditions match the type of data that the field returns.
        
        Correct Structure of Logic Conditions: The vars array should have the correct structure and number of items as per the operation's requirements.
        
        Similarly, check and update other conditions in your logic section following the same principles. Make sure that the operations and the types of values in your conditions are compatible with each other and with the field types they reference.
        
        shape is never a valid field detail or option, never include it. Take your time, go slow, and produce only valid json.
        ALWAYS Ensure that the ref values used in your logic section match exactly with the ref values defined in your fields.
        ####
        
        You will be considered to have failed if your survey is less than 20 questions or if you return incomplete or invalid json.
        Your survey should be at least 4000 words. Your Complete Json for the 20 question Survey Based on the Corpus:"""}],
        
        
              max_tokens=4000,
              temperature=0.2,
              response_format={ "type": "json_object" }
        )
        
        #print(response.choices[0].message.content)


        import re
        import json
        
        def replace_bool(match):
            if match.group(0) == 'true':
                return 'True'
            else:
                return 'False'
        
        gpt_json = response.choices[0].message.content
        result = re.sub(r'\btrue\b|\bfalse\b', replace_bool, gpt_json)
        
        #print(result)
        json_object = json.loads(response.choices[0].message.content)




        endpoint = 'https://api.typeform.com/forms'
    
        
        def create_form(api_token, form_data):
            """
            Create a new form on Typeform using the provided API token and form data.
        
            Parameters:
            api_token (str): Typeform API token for authentication.
            form_data (dict): The data for the form to be created.
        
            Returns:
            dict: JSON response containing the created form data.
            """
            endpoint = 'https://api.typeform.com/forms'
            headers = {
                'Authorization': f'Bearer {api_token}',
                'Content-Type': 'application/json'
            }
        
            try:
                response = requests.post(endpoint, json=form_data, headers=headers)
                #print(response.content)
                response.raise_for_status()  # Raises an HTTPError if the HTTP request returned an unsuccessful status code
                return response.json()
            except requests.RequestException as e:
                print(f"An error occurred: {e}")
                return None
        
        
        
        created_form = create_form(api_token, json_object)
        progress.progress(95)
        # Create a zip archive
        with zipfile.ZipFile('All_Results.zip', 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Save the outline string as a text file and add to the zip archive
            outline_file_name = 'Final_Outline.txt'
            save_string_to_file(str(outline), outline_file_name)
            zipf.write(outline_file_name, outline_file_name)

            # Save the final article
            final_article_file_name = 'Final_Article.txt'
            save_string_to_file(str(final_article), final_article_file_name)
            zipf.write(final_article_file_name, final_article_file_name)
        
            # Save the full notes as a CSV file and add to the zip archive
            full_notes_file_name = 'Full_Notes.csv'
            save_bytes_to_file(aggregate_notes_csv_bytes, full_notes_file_name)
            zipf.write(full_notes_file_name, full_notes_file_name)
        
            # Save the all outlines as a CSV file and add to the zip archive
            all_outlines_file_name = 'All_Outlines.csv'
            save_bytes_to_file(all_outlines_csv_bytes, all_outlines_file_name)
            zipf.write(all_outlines_file_name, all_outlines_file_name)
        
        
        progress.progress(100)
        st.markdown(final_article)
        if created_form:
            survey_form = json.dumps(created_form, indent=4)
            st.write(survey_form)
        with open("All_Results.zip", "rb") as fp:
            btn = st.download_button(
                label="Download ZIP",
                data=fp,
                file_name="All_Results.zip",
                mime="application/zip"
            )

        print('Successfully created All_Results.zip')
    
        st.success("Research, outline, and final article generation completed successfully.")

if __name__ == "__main__":
    main()










