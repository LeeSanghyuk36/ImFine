from fastapi import FastAPI, APIRouter, Request, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import requests
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os
import pika
from celery import Celery
from celery.result import AsyncResult

# 직접 gpt2 모델을 파인튜닝하기 위한 라이브러리
from pydantic import BaseModel
from transformers import GPT2LMHeadModel, GPT2Tokenizer

# GPU를 사용하지 않겠다
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

app = FastAPI() # 인스턴스 생성

# html을 사용하기 위해 Jinja2 함수 사용
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

# html에서 static 폴더를 마운트할 때 사용
app.mount("/static", StaticFiles(directory="static"), name="static")

router = APIRouter()  # APIRouter 인스턴스 생성
colab_url = ""
origins = [
    # "http://192.168.0.13:3000", # url을 등록해도 되고
    "*" # private 영역에서 사용한다면 *로 모든 접근을 허용할 수 있다
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 혹은 특정 도메인을 추가
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)

# Celery 인스턴스 생성
celery_app = Celery('tasks', broker='amqp://guest:guest@rabbitmq:5672')

# RabbitMQ 설정
rabbitmq_host = 'rabbitmq'  # RabbitMQ 서버의 호스트
queue_name = 'test'    # 사용할 큐 이름

# RabbitMQ 연결
connection = pika.BlockingConnection(pika.ConnectionParameters(host='rabbitmq'))
channel = connection.channel()

# 큐 선언
channel.queue_declare(queue=queue_name)

# 직접 gpt2 모델을 파인튜닝할 때 사용할 인자 값
class FTRequest(BaseModel):
    input_text: str
    mlp_weight: float
    attn_weight: float
    eps_weight: float

# 직접 gpt2 모델을 파인튜닝 하는 API
@router.post("/gpt-fine-tune")
async def gpt_fine_tune(request: FTRequest):
    model_name = "gpt2"
    tokenizer = GPT2Tokenizer.from_pretrained(model_name)
    model = GPT2LMHeadModel.from_pretrained(model_name)

    input_ids = tokenizer.encode(request.input_text, return_tensors="pt")
    for i in range(12):
        model.transformer.h[i].attn.c_attn.weight.data *= request.attn_weight
    for j in range(12):
        model.transformer.h[j].mlp.c_fc.weight.data *= request.mlp_weight
    for k in range(12):
        model.transformer.h[k].ln_1.eps *= 10 ** (request.eps_weight - 1)
    for l in range(12):
        model.transformer.h[l].ln_2.eps *= 10 ** (request.eps_weight - 1)

    output = model.generate(input_ids, max_length=100, num_return_sequences=1, no_repeat_ngram_size=2)
    generated_text = tokenizer.decode(output[0], skip_special_tokens=True)
    return {"result": generated_text}    

# 마이크로 사이트 API - html 사용
@router.get("/", response_class=HTMLResponse)
async def micro(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# 코랩 주소 업데이트
@router.post("/set-url")
async def setUrl(request: Request):
    data = await request.json() 
    url = data["url"]
    global colab_url
    colab_url = url
    return colab_url

@router.get("/get-url")
def getUrl():
    return colab_url

#gpt 모델 가중치 변경해서 데이터 반환
# @router.post("/gpt")
# async def call_colab_function(request: Request):
#     json_data = await request.json()
#     response = requests.post(colab_url, json=json_data)
#     return response.text

@router.post("/llama-fine-tune")
async def call_colab_llama(request: Request):
    json_data = await request.json()
    response = requests.post(colab_url + "/llama", json=json_data)
    return response.text

###########################################################################
###########################################################################


@router.post("/gpt")
async def send_message(request: Request):
    # JSON 데이터 추출
    json_data = await request.json()
    print(json_data)
    # Celery 작업 호출
    task = celery_app.send_task('tasks.gpt', args=[json_data, colab_url])
    return {"message": "Task sent", "task_id": task.id}

@router.post("/llama")
async def receive_message(request: Request):
     # JSON 데이터 추출
    json_data = await request.json()
    print(json_data)
    # Celery 작업 호출
    task = celery_app.send_task('tasks.llama', args=[json_data, colab_url])
    return {"message": "Task sent", "task_id": task.id}

@router.get("/logs")
async def get_logs():
    task = celery_app.send_task('tasks.getLogs', args=[])
    result = AsyncResult(task.id).get(timeout=10)  # 여기서 10초 동안 결과를 기다립니다.
    return result

app.include_router(router, prefix="/api")  # "/api" 접두사와 함께 router를 app에 포함

