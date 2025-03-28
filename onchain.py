#address based function
import pandas as pd
import requests
import time
from datetime import datetime, timedelta
import os
import shutil
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager
import json

def get_transactions_mempool(address, after_txid=None, max_retries=3):
    """거래소 주소의 트랜잭션 데이터를 가져옴 (자동 재시도 포함)"""
    url = f"https://mempool.space/api/address/{address}/txs"
    if after_txid:
        url += f"?after_txid={after_txid}"

    for attempt in range(max_retries):  # ✅ 최대 3회 재시도
        try:
            response = requests.get(url, timeout=10, stream=True)  # ✅ `stream=True` 추가
            response.raise_for_status()  # ✅ HTTP 오류 발생 시 예외 발생
            return response.json()
        
        except requests.exceptions.ChunkedEncodingError:
            print(f"⚠️ ChunkedEncodingError 발생. {attempt + 1}/{max_retries}회 재시도 중...")
            time.sleep(3)  # ✅ 재시도 전 3초 대기
        
        except requests.exceptions.RequestException as e:
            print(f"❌ 요청 오류 발생: {e}")
            break  # ✅ 다른 요청 오류 발생 시 중단
    
    return []  # ✅ 모든 재시도 실패 시 빈 리스트 반환

def load_addr():
    # CSV 파일을 불러와 데이터프레임으로 변환
    cex_loaded = pd.read_csv('cex_address.csv')

    # 데이터프레임을 다시 리스트로 변환
    all_exchange_addresses = cex_loaded.values.tolist()

    # 🔹 모든 데이터를 저장할 리스트
    data = []

    def process_transactions(address, max_transactions=1000):
        """거래소 지갑의 트랜잭션 데이터를 분석"""
        transactions = []
        after_txid = None  

        while len(transactions) < max_transactions:
            batch = get_transactions_mempool(address, after_txid)
            
            if not batch:
                break  # ✅ 더 이상 가져올 트랜잭션이 없으면 종료
            
            transactions.extend(batch)
            after_txid = batch[-1]['txid']  # ✅ 가장 오래된 트랜잭션 ID 저장
            
            time.sleep(1)  # ✅ API 요청 속도 제한 고려 (1초 대기)

        for tx in transactions[:max_transactions]:  
            sent_amount = 0
            received_amount = 0
            
            for vin in tx['vin']:
                if vin.get('prevout') and 'scriptpubkey_address' in vin['prevout']:
                    if vin['prevout']['scriptpubkey_address'] == address:
                        sent_amount += vin['prevout']['value']  

            for vout in tx['vout']:
                if 'scriptpubkey_address' in vout:
                    if vout['scriptpubkey_address'] == address:
                        received_amount += vout['value']  

            date = datetime.datetime.fromtimestamp(tx['status']['block_time']).strftime('%Y-%m-%d %H:%M:%S') if 'block_time' in tx['status'] else "Unconfirmed"

            data.append({
                "date": date,
                "wallet_address": address,
                "sent_amount_btc": sent_amount / 1e8,  
                "received_amount_btc": received_amount / 1e8  
            })

    # 🔹 거래소 주소 목록 처리
    for address in [addr[0] for addr in all_exchange_addresses]:
        process_transactions(address, max_transactions=1000)    

    df_exchange_transactions = pd.DataFrame(data)
    df_exchange_transactions.sort_values(by='date', inplace=True)

    for i in range(len(df_exchange_transactions.index)):
        if df_exchange_transactions.loc[i, 'date'] == 'Unconfirmed':
            df_exchange_transactions.drop(index=[i], inplace=True)
    df_exchange_transactions['net'] = df_exchange_transactions['sent_amount_btc'] - df_exchange_transactions['received_amount_btc']

    df_exchange_transactions.to_csv('cex_transactions.csv')

    df_daily_summary = df_exchange_transactions.groupby("date", as_index=False).agg({
    "sent_amount_btc": "sum",
    "received_amount_btc": "sum",
    "net": "sum"
    })

    # ✅ 결과 출력
    df_daily_summary.to_csv('day_sum.csv')


#Hash, UTXO
def Hash_UTXO():

    def fetch_network_data(metric, start_date, end_date):
        """Blockchain Data API에서 네트워크 및 채굴 데이터를 가져오는 함수"""
        url = f"https://api.blockchain.info/charts/{metric}?timespan=10years&format=json&sampled=false"
        response = requests.get(url)
        data = response.json()

        if "values" not in data:
            print(f"⚠️ API 응답 오류: '{metric}' 데이터에 'values' 키가 없습니다.")
            return pd.DataFrame()
        
        timestamps = [entry["x"] for entry in data["values"]]
        values = [entry["y"] for entry in data["values"]]

        df = pd.DataFrame({"timestamp": timestamps, metric: values})
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")

        # 날짜 범위 필터링
        df = df[(df["timestamp"] >= start_date) & (df["timestamp"] <= end_date)]
        
        return df

    # 5년 데이터 요청
    start_date = datetime.now() - timedelta(days=1825)
    end_date = datetime.now()

    # 가져올 네트워크 관련 데이터
    network_metrics = ["hash-rate", "difficulty"]
    df_network = fetch_network_data(network_metrics[0], start_date, end_date)

    for metric in network_metrics[1:]:
        temp_df = fetch_network_data(metric, start_date, end_date)
        if not temp_df.empty:
            df_network = df_network.merge(temp_df, on="timestamp", how="left")

    # 데이터 저장
    df_network.to_csv("bitcoin_network_data.csv", index=False)

    def fetch_utxo_data(metric, start_date, end_date):
        """Blockchain Data API에서 UTXO 관련 데이터를 가져오는 함수"""
        url = f"https://api.blockchain.info/charts/{metric}?timespan=5years&format=json&sampled=false"
        response = requests.get(url)
        data = response.json()

        if "values" not in data:
            print(f"⚠️ API 응답 오류: '{metric}' 데이터에 'values' 키가 없습니다.")
            return pd.DataFrame()
        
        timestamps = [entry["x"] for entry in data["values"]]
        values = [entry["y"] for entry in data["values"]]

        df = pd.DataFrame({"timestamp": timestamps, metric: values})
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")

        # 날짜 범위 필터링
        df = df[(df["timestamp"] >= start_date) & (df["timestamp"] <= end_date)]
        
        return df

    # 가져올 UTXO 관련 데이터
    utxo_metrics = ["utxo-count", "total-bitcoins"]
    df_utxo = fetch_utxo_data(utxo_metrics[0], start_date, end_date)

    # 데이터 저장
    df_utxo.to_csv("bitcoin_utxo_data.csv", index=False)



#MVRV
def MVRV():
    # 🔹 다운로드 경로 설정
    download_dir = r"C:\Users\user\Downloads"  # 기본 다운로드 폴더
    destination_dir = r"C:\Users\user\btc\btc-auto-trader"  # 최종 저장 폴더. 사용 시 다운로드 폴더 위치 변경 필요

    # 🔹 Chrome 옵션 설정
    chrome_options = Options()
    chrome_options.add_experimental_option("prefs", {
        "download.default_directory": download_dir,  # 다운로드 경로 설정
        "download.prompt_for_download": False,  # 다운로드 시 확인창 비활성화
        "directory_upgrade": True,
        "safebrowsing.enabled": True
    })

    # 🔹 Chrome WebDriver 실행
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    try:
        # 🔹 MVRV 데이터 페이지 열기
        url = "https://www.blockchain.com/explorer/charts/mvrv"
        driver.get(url)

        # 🔹 페이지 로딩 대기
        time.sleep(5)

        # 🔹 "All" 버튼 클릭 (전체 기간 선택)
        try:
            all_button = driver.find_element(By.XPATH, "//button[contains(text(), '3Y')]")
            ActionChains(driver).move_to_element(all_button).click().perform()
            print("✅ 'All' 버튼 클릭 완료")
        except Exception as e:
            print("⚠ 'All' 버튼 클릭 실패:", e)

        # 🔹 "Download JSON" 버튼 클릭
        time.sleep(10)  # 버튼 활성화 대기
        try:
            download_button = driver.find_element(By.XPATH, "//div[contains(text(), 'Download JSON')]")
            ActionChains(driver).move_to_element(download_button).click().perform()
            print("✅ 'Download JSON' 버튼 클릭 완료")
        except Exception as e:
            print("⚠ 'Download JSON' 버튼 클릭 실패:", e)

        # 🔹 다운로드 완료 대기
        time.sleep(10)

        # 🔹 다운로드된 JSON 파일 찾기 및 이동
        downloaded_files = [f for f in os.listdir(download_dir) if f.endswith('.json')]
        if downloaded_files:
            latest_file = max([os.path.join(download_dir, f) for f in downloaded_files], key=os.path.getctime)
            shutil.move(latest_file, os.path.join(destination_dir, os.path.basename(latest_file)))
            print(f"✅ 파일이 성공적으로 이동됨: {latest_file} → {destination_dir}")
        else:
            print("⚠ 다운로드된 JSON 파일을 찾을 수 없음.")

    finally:
        # 🔹 브라우저 종료
        driver.quit()

    # 파일 경로
    file_path = "./mvrv.json"

    # JSON 파일 로드
    with open(file_path, "r") as file:
        data = json.load(file)

    # MVRV 데이터 추출
    mvrv_data = data["mvrv"]
    df = pd.DataFrame(mvrv_data)

    # 타임스탬프를 날짜로 변환
    df["date"] = pd.to_datetime(df["x"], unit="ms")
    df["MVRV"] = df["y"]
    df = df[["date", "MVRV"]]  # 필요한 컬럼만 유지

    # CSV 파일 저장
    csv_path = "./mvrv_ratio.csv"
    df.to_csv(csv_path, index=False)

#파일 병합
def allinone():
    ntwrk = pd.read_csv('bitcoin_network_data.csv')
    utxo = pd.read_csv('bitcoin_utxo_data.csv')
    mvrv = pd.read_csv('mvrv_ratio.csv')
    day_sum = pd.read_csv('day_sum.csv')