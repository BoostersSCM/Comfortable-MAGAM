import streamlit as st
import os
import time
import re
import base64
import json
import tempfile
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import pdfplumber
from streamlit_google_auth import Authenticate



# --- 1. 구글 OAuth 설정 (임시 JSON 파일 생성 방식) ---
# 라이브러리가 요구하는 표준 JSON 구조를 만듭니다.
google_creds = {
    "web": {
        "client_id": st.secrets["google_auth"]["client_id"],
        "client_secret": st.secrets["google_auth"]["client_secret"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "redirect_uris": [st.secrets["google_auth"]["redirect_uri"]]
    }
}

# 임시 폴더에 json 파일을 저장하고 그 경로를 가져옵니다.
with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as temp_cred_file:
    json.dump(google_creds, temp_cred_file)
    temp_cred_path = temp_cred_file.name

# 수정된 Authenticate 호출 (인자 이름을 라이브러리 규격에 맞춤)
auth = Authenticate(
    secret_credentials_path = temp_cred_path,  # 파일 경로를 전달
    cookie_name = "boosters_tax_auth",
    key = st.secrets["google_auth"]["cookie_key"],
    cookie_expiry_days = 1,
    redirect_uri = st.secrets["google_auth"]["redirect_uri"]
)

# --- 2. PDF 정보 추출 함수 (기존 로직 이식) ---
def extract_info_from_pdf(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[0].extract_text()
            lines = text.split('\n')
            
            회사명 = ""
            for i, line in enumerate(lines):
                if '상호' in line or '법인명' in line:
                    parts = line.split()
                    for j, part in enumerate(parts):
                        if '상호' in part or '법인명' in part:
                            if j + 1 < len(parts):
                                회사명_parts = parts[j+1:]
                                for k, word in enumerate(회사명_parts):
                                    if '성명' in word:
                                        회사명_parts = 회사명_parts[:k]
                                        break
                                회사명 = ' '.join(회사명_parts)
                                break
                    break
            
            정산일자 = ""
            date_pattern = r'(\d{4})[년\s]*(\d{1,2})[월\s]*(\d{1,2})[일\s]*'
            matches = re.findall(date_pattern, text)
            if matches:
                year, month, day = matches[0]
                정산일자 = f"{year}{month.zfill(2)}{day.zfill(2)}"
            
            return 회사명.strip(), 정산일자
    except Exception:
        return "", ""

# --- 3. Selenium 드라이버 설정 (Headless) ---
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver

# --- 4. 메인 인증 로직 ---
auth.check_authentification()
auth.login()

if st.session_state.get('connected'):
    user_email = st.session_state['user_info'].get('email', '')
    
    # 도메인 제한 체크 (@boosters.kr)
    if not user_email.endswith("@boosters.kr"):
        st.error(f"접근 권한이 없습니다: {user_email}")
        st.warning("@boosters.kr 계정으로 다시 로그인해주세요.")
        if st.button("로그아웃"):
            auth.logout()
        st.stop()

    # --- 서비스 본문 시작 ---
    st.sidebar.success(f"접속됨: {user_email}")
    if st.sidebar.button("로그아웃"):
        auth.logout()

    st.title("📄 세금계산서 PDF 변환기 (Boosters 전용)")
    st.info("HTML 파일을 업로드하면 비밀번호를 자동으로 입력하고 회사명을 추출하여 PDF로 변환합니다.")

    uploaded_files = st.file_uploader("HTML 파일 선택", type="html", accept_multiple_files=True)
    사업자번호 = st.text_input("비밀번호 (사업자번호)", value="1828801269")

    if st.button("변환 프로세스 시작") and uploaded_files:
        driver = get_driver()
        progress_bar = st.progress(0)
        
        for idx, uploaded_file in enumerate(uploaded_files):
            try:
                # 1. 임시 파일 저장
                with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp_html:
                    tmp_html.write(uploaded_file.getvalue())
                    tmp_path = tmp_html.name

                # 2. 브라우저 조작
                driver.get(f"file://{tmp_path}")
                wait = WebDriverWait(driver, 10)
                
                # 비번 입력 및 확인
                pw_input = wait.until(EC.presence_of_element_located((By.XPATH, '//input[@type="password"]')))
                pw_input.send_keys(사업자번호)
                driver.find_element(By.XPATH, '//button[contains(text(), "확인")]').click()
                time.sleep(4) # 렌더링 대기

                # 3. PDF 저장 (CDP 명령어 사용)
                pdf_params = {'printBackground': True, 'pageSize': 'A4'}
                pdf_data = driver.execute_cdp_cmd("Page.printToPDF", pdf_params)
                pdf_bytes = base64.b64decode(pdf_data['data'])

                # 4. 정보 추출을 위해 임시 PDF 저장
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                    tmp_pdf.write(pdf_bytes)
                    tmp_pdf_path = tmp_pdf.name
                
                회사명, 정산일자 = extract_info_from_pdf(tmp_pdf_path)
                
                # 파일명 생성
                today = datetime.today().strftime("%Y%m%d")
                safe_회사명 = re.sub(r'[\\/*?:"<>|]', "_", 회사명) if 회사명 else "알수없음"
                final_filename = f"세금계산서_{safe_회사명}_{정산일자 or today}.pdf"

                # 5. 결과물 제공
                st.success(f"✅ 변환 완료: {final_filename}")
                st.download_button(
                    label=f"📥 {final_filename} 다운로드",
                    data=pdf_bytes,
                    file_name=final_filename,
                    mime="application/pdf",
                    key=f"btn_{idx}"
                )
                
                # 임시 파일 삭제
                os.unlink(tmp_path)
                os.unlink(tmp_pdf_path)

            except Exception as e:
                st.error(f"❌ {uploaded_file.name} 처리 중 오류: {str(e)}")
            
            progress_bar.progress((idx + 1) / len(uploaded_files))
        
        driver.quit()
        st.balloons()

else:
    st.info("서비스를 이용하려면 @boosters.kr 계정으로 로그인하세요.")
