import streamlit as st
import tempfile
import os
import re
import base64
import time
import requests
import shutil
from bs4 import BeautifulSoup

from authlib.integrations.requests_client import OAuth2Session
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# =====================================================
# 1. Google OAuth (기존 유지)
# =====================================================
def require_login():
    if "user_email" in st.session_state:
        return st.session_state["user_email"]

    client_id = st.secrets["google"]["client_id"]
    client_secret = st.secrets["google"]["client_secret"]
    redirect_uri = st.secrets["google"]["redirect_uri"]

    oauth = OAuth2Session(
        client_id=client_id,
        client_secret=client_secret,
        scope="openid email profile",
        redirect_uri=redirect_uri,
    )

    code = st.query_params.get("code")

    if not code:
        auth_url, _ = oauth.create_authorization_url(
            "https://accounts.google.com/o/oauth2/auth",
            access_type="offline",
            prompt="consent",
        )
        st.title("🔐 로그인 필요")
        st.info("@boosters.kr 계정으로 로그인해 주세요.")
        st.link_button("Google 계정으로 로그인", auth_url)
        st.stop()

    try:
        token = oauth.fetch_token(
            "https://oauth2.googleapis.com/token",
            code=code,
            authorization_response=redirect_uri + "?code=" + code
        )

        userinfo_endpoint = "https://openidconnect.googleapis.com/v1/userinfo"
        headers = {'Authorization': f"Bearer {token['access_token']}"}
        resp = requests.get(userinfo_endpoint, headers=headers)
        userinfo = resp.json()
        email = userinfo.get("email", "").lower()

        if not email.endswith("@boosters.kr"):
            st.error(f"🚫 접근 권한이 없습니다: {email}")
            st.stop()

        st.session_state["user_email"] = email
        st.query_params.clear()
        st.rerun()
        
    except Exception as e:
        st.error(f"인증 처리 중 오류가 발생했습니다: {str(e)}")
        if st.button("다시 로그인 시도"):
            st.query_params.clear()
            st.rerun()
        st.stop()

# =====================================================
# 2. HTML 정보 추출 (날짜 로직 수정: 아래 칸 + YYYY/MM/DD)
# =====================================================
def extract_info_from_html_content(html_content):
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        
        회사명 = ""
        정산일자 = ""

        # [1] 회사명 추출 (공급자 칸 오른쪽 확인)
        target_cells = soup.find_all(lambda tag: tag.name in ['td', 'th'] and ('상호' in tag.get_text() or '법인명' in tag.get_text()))
        for cell in target_cells:
            siblings = cell.find_next_siblings(['td', 'th'])
            for sibling in siblings:
                val = sibling.get_text(strip=True)
                if not val: continue
                if any(k in val for k in ["성명", "대표자", "등록번호", "사업자"]): break
                
                회사명 = val.replace("(", "").replace(")", "").replace("법인명", "").strip()
                break
            if 회사명: break

        # [2] 정산일자 추출 (작성일자 아래 칸 & YYYY/MM/DD 포맷)
        # 작성일자라고 적힌 셀을 찾습니다.
        date_label_cells = soup.find_all(lambda tag: tag.name in ['td', 'th'] and ('작성' in tag.get_text() and '일자' in tag.get_text()))
        
        for cell in date_label_cells:
            # 현재 셀이 속한 행(tr)을 찾습니다.
            current_row = cell.find_parent('tr')
            if current_row:
                # 바로 다음 행(Next Row)을 찾습니다.
                next_row = current_row.find_next_sibling('tr')
                if next_row:
                    # 다음 행의 텍스트 전체에서 날짜 패턴(YYYY/MM/DD)을 찾습니다.
                    row_text = next_row.get_text()
                    # 슬래시(/) 구분자 패턴 적용
                    match = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", row_text)
                    if match:
                        y, m, d = match.groups()
                        정산일자 = f"{y}{m.zfill(2)}{d.zfill(2)}"
                        break
        
        # 만약 표 구조로 못 찾았다면, 전체 텍스트에서 YYYY/MM/DD 패턴 백업 검색
        if not 정산일자:
            text_content = soup.get_text()
            match = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", text_content)
            if match:
                y, m, d = match.groups()
                정산일자 = f"{y}{m.zfill(2)}{d.zfill(2)}"

        return 회사명.strip(), 정산일자

    except Exception as e:
        print(f"Parsing Error: {e}")
        return "", ""

# =====================================================
# 3. Selenium 설정
# =====================================================
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--lang=ko_KR") 

    options.binary_location = "/usr/bin/chromium"
    service = Service("/usr/bin/chromedriver")
    
    driver = webdriver.Chrome(service=service, options=options)
    return driver

# =====================================================
# 4. 앱 실행 로직 (다운로드 상태 유지 기능 추가)
# =====================================================
st.set_page_config(page_title="Boosters Tax Converter", page_icon="📄")
user_email = require_login()

st.sidebar.success(f"✅ 로그인됨\n{user_email}")
if st.sidebar.button("로그아웃"):
    st.session_state.clear()
    st.rerun()

st.title("📄 세금계산서 PDF 변환기 (Boosters)")

# [중요] 변환된 파일 정보를 저장할 세션 초기화
if "processed_files" not in st.session_state:
    st.session_state.processed_files = []

uploaded_files = st.file_uploader("HTML 파일 선택 (다중 선택 가능)", type="html", accept_multiple_files=True)
biz_num = st.text_input("비밀번호 (사업자번호)", value="1828801269")

# 변환 버튼 클릭 시 로직
if st.button("🚀 변환 시작") and uploaded_files:
    # 기존 결과 초기화 (새로 변환하니까)
    st.session_state.processed_files = []
    
    driver = get_driver()
    progress_bar = st.progress(0)
    
    for idx, f in enumerate(uploaded_files):
        with st.status(f"처리 중 ({idx+1}/{len(uploaded_files)}): {f.name}") as status:
            try:
                # HTML 읽기
                raw_bytes = f.getvalue()
                try:
                    html_content = raw_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        html_content = raw_bytes.decode('euc-kr')
                    except:
                        html_content = raw_bytes.decode('cp949', errors='ignore')

                # 정보 추출
                회사명, 정산일자 = extract_info_from_html_content(html_content)
                
                # 폰트 스타일 삽입
                font_style = """
                <style>
                    @import url('https://fonts.googleapis.com/css2?family=Nanum+Gothic:wght@400;700&display=swap');
                    body, table, td, span, div, p, input { 
                        font-family: 'NanumGothic', 'Nanum Gothic', 'Malgun Gothic', sans-serif !important; 
                    }
                </style>
                <meta charset="utf-8">
                """
                if "<head>" in html_content.lower():
                    html_content = html_content.replace("<head>", "<head>" + font_style, 1)
                else:
                    html_content = font_style + html_content

                # 임시 파일 생성
                with tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode='w', encoding='utf-8') as tmp:
                    tmp.write(html_content)
                    h_path = tmp.name

                # Selenium 실행
                driver.get(f"file://{h_path}")
                wait = WebDriverWait(driver, 10)
                
                try:
                    pw = wait.until(EC.presence_of_element_located((By.XPATH, '//input[@type="password"]')))
                    pw.send_keys(biz_num)
                    driver.find_element(By.XPATH, '//button[contains(text(),"확인")]').click()
                    time.sleep(5) 
                except:
                    pass 

                # PDF 생성
                pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                    "printBackground": True,
                    "paperWidth": 8.27,
                    "paperHeight": 11.69
                })
                pdf_bytes = base64.b64decode(pdf_data["data"])
                
                # 파일명 생성
                if not 회사명: 회사명 = "상호미상"
                safe_name = re.sub(r'[\\/*?:"<>|]', "_", 회사명)
                fn = f"세금계산서_{safe_name}_{정산일자}.pdf" if 정산일자 else f"세금계산서_{safe_name}_{int(time.time())}.pdf"
                
                # [핵심 변경] 바로 다운로드 버튼을 띄우지 않고, 세션에 저장합니다.
                st.session_state.processed_files.append({
                    "file_name": fn,
                    "data": pdf_bytes,
                    "original_name": f.name
                })
                
                status.update(label=f"✅ 변환 완료: {fn}", state="complete")
                os.unlink(h_path)
                
            except Exception as e:
                st.error(f"오류 ({f.name}): {str(e)}")
        
        progress_bar.progress((idx + 1) / len(uploaded_files))

    driver.quit()
    st.success("모든 변환이 완료되었습니다! 아래에서 파일을 다운로드하세요.")

# [중요] 변환 루프 밖에서 다운로드 버튼 생성 (화면이 리프레시되어도 유지됨)
if st.session_state.processed_files:
    st.write("---")
    st.subheader(f"📥 변환된 파일 목록 ({len(st.session_state.processed_files)}개)")
    
    for i, file_info in enumerate(st.session_state.processed_files):
        col1, col2 = st.columns([3, 1])
        with col1:
            st.write(f"📄 {file_info['file_name']}")
        with col2:
            st.download_button(
                label="다운로드",
                data=file_info["data"],
                file_name=file_info["file_name"],
                mime="application/pdf",
                key=f"download_btn_{i}"
            )
            
    # 전체 초기화 버튼
    if st.button("목록 초기화"):
        st.session_state.processed_files = []
        st.rerun()
