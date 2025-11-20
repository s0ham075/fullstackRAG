from langchain_astradb import AstraDBVectorStore
from astrapy import DataAPIClient
from astrapy.constants import VectorMetric
from astrapy.info import CollectionDefinition, CollectionVectorOptions
from langchain_classic.retrievers.multi_query import MultiQueryRetriever
from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from azure_utils import secret_client,get_blob_data
from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_classic.retrievers.document_compressors import LLMChainExtractor
from langchain_classic.chains import RetrievalQA
from langchain_classic.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
import joblib
import os
import re
def remove_pdf_extension(file_name):
    """Remove PDF extension and sanitize collection name for AstraDB"""
    # Remove .pdf extension
    if file_name.endswith(".pdf"):
        name = file_name[:-4]
    else:
        name = file_name
    
    name = name.replace(" ", "_").replace("-", "_")
    name = re.sub(r'[^a-zA-Z0-9_]', '', name)
    if name and name[0].isdigit():
        name = "col_" + name
    name = name[:48]
    if not name:
        name = "collection_" + str(hash(file_name))[:8]
    return name

# def load_or_download_model(model_name, cache_file):
#     try:
#         embeddings = joblib.load(cache_file)
#         print("Model loaded from cache.")
#     except FileNotFoundError:
#         embeddings = FastEmbedEmbeddings(model_name=model_name)
#         joblib.dump(embeddings, cache_file)
#         print("Model downloaded and cached.")

#     return embeddings

# # Define your model name and cache file path
# model_name = "BAAI/bge-small-en-v1.5"
# cache_file = "cached_model.joblib"


# Load or download the model
# embeddings = load_or_download_model(model_name, cache_file)
embeddings = FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")
vectorstore = None
astradbendpoint = secret_client.get_secret("astradbendpoint")
astradbtoken = secret_client.get_secret("astratoken")
openrouter_api_key = secret_client.get_secret("openrouterapikey").value

# Set base URL (OpenRouter requirement)
os.environ["OPENAI_API_BASE"] = "https://openrouter.ai/api/v1"
os.environ["OPENAI_API_KEY"] = openrouter_api_key
client = DataAPIClient(token=astradbtoken.value)
db = client.get_database_by_api_endpoint(astradbendpoint.value)

def embed_blob(blob_name):
    collection_names = db.list_collection_names()
    target = remove_pdf_extension(blob_name)
        
    # Check if collection already exists
    if target in collection_names:
        print(f"Collection '{target}' already exists")
        return True
        
    # Create collection definition with vector options
    collection_definition = CollectionDefinition(
        vector=CollectionVectorOptions(
            dimension=384,  # BAAI/bge-small-en-v1.5 uses 384 dimensions
            metric=VectorMetric.COSINE,
        ),
    )
        
    # Create new collection
    collection = db.create_collection(
        target,
        definition=collection_definition,
    )
    print(f"Collection '{target}' created")

    vectorstore = AstraDBVectorStore(
        embedding=embeddings,
        collection_name=remove_pdf_extension(blob_name),
        api_endpoint= secret_client.get_secret('astradbendpoint').value,
        token=secret_client.get_secret('astratoken').value,
    )
    print("collection created")

    try:
        chunks = get_blob_data(blob_name)
        print("chunks created")
        vectorstore.add_documents(chunks)
        return True
    except Exception as e:
        print(f"Error loading and indexing repository: {e}") 
        return False
    
B_INST, E_INST = "[INST]", "[/INST]"
B_SYS, E_SYS = "<<SYS>>\n", "\n<</SYS>>\n\n"

def get_prompt(instruction, new_system_prompt ):
    SYSTEM_PROMPT = B_SYS + new_system_prompt + E_SYS
    prompt_template =  B_INST + SYSTEM_PROMPT + instruction + E_INST
    return prompt_template

sys_prompt = """You are a helpful, smart and intelligent assistant. Always answer as helpfully as possible. You can take the help of context provided however if the question and context don't seem to match do not use the context, if the user is asking for previous message or context,
If a question does not make any sense, or is not factually coherent, explain why instead of answering something not correct. If you don't know the answer to a question, please don't share false information. """

instruction = """
CONTEXT:/n/n {context}/n

Question: {question}"""


prompt_template = get_prompt(instruction, sys_prompt)
llama_prompt = PromptTemplate(
    template=prompt_template, input_variables=["context", "question"]
)
llama2_llm = ChatOpenAI(
    model="meta-llama/llama-3.3-70b-instruct:free",
    temperature=0.7,
    max_tokens=1024,
)

llm = ChatOpenAI(
    model="meta-llama/llama-3.3-70b-instruct:free",
    temperature=0,
    max_tokens=1024,
)
compressor = LLMChainExtractor.from_llm(llm)

def process_llm_response(llm_response):
  response = " "
#   response += llm_response['result'] + "\n\nSources\n"
#   for source in llm_response['source_documents']:
#      response +="Source - "+source.metadata['source'] +"\n"

  return llm_response['result']

def answer_query(blob_name,query,retriever_type):
    vectorstore = AstraDBVectorStore(
    embedding=embeddings,
    collection_name=remove_pdf_extension(blob_name),
    api_endpoint= astradbendpoint.value,
    token=astradbtoken.value,
    )
    qa_chain = None
    retriever = vectorstore.as_retriever(search_type='mmr')
    retriever_to_be_used = None
    if(retriever_type=="multiretriever"):
        multi_retriever = MultiQueryRetriever.from_llm(
           retriever=retriever, llm=llm
        )
        retriever_to_be_used = multi_retriever

    elif(retriever_type=="compressionretriever"):
        compression_retriever = ContextualCompressionRetriever(
            base_compressor=compressor, base_retriever=retriever
       )
        retriever_to_be_used = compression_retriever

    else :
       retriever_to_be_used = retriever

    qa_chain = RetrievalQA.from_chain_type(
        llm= llama2_llm, 
        verbose=True,
        chain_type="stuff",
        retriever= retriever_to_be_used,
        chain_type_kwargs = {"prompt": llama_prompt}
    )
    print("retriever used :",retriever_to_be_used)
    return process_llm_response(qa_chain(query))


