import streamlit as st
import tempfile
import os
import re
import base64
import time
import requests
import shutil
from bs4 import BeautifulSoup  # HTML 구조 분석을 위한 핵심 라이브러리

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
# 2. [강력해진] HTML 표 구조 기반 정보 추출
# =====================================================
def extract_info_from_html_content(html_content):
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        
        회사명 = ""
        정산일자 = ""

        # -------------------------------------------------
        # [1] 회사명 추출 (빈 칸 건너뛰기 로직 추가)
        # -------------------------------------------------
        # '상호' 글자가 포함된 모든 칸을 찾습니다. (공급자, 공급받는자)
        target_cells = soup.find_all(lambda tag: tag.name in ['td', 'th'] and ('상호' in tag.get_text() or '법인명' in tag.get_text()))
        
        for cell in target_cells:
            # 이 칸이 '공급받는자' 쪽이면 무시하고 싶을 수 있으나, 
            # 보통 문서 상단(먼저 나오는 것)이 '공급자'입니다.
            
            # 현재 칸의 오른쪽 형제들을 모두 가져옵니다.
            siblings = cell.find_next_siblings(['td', 'th'])
            
            for sibling in siblings:
                val = sibling.get_text(strip=True) # 공백 제거 후 텍스트 확인
                
                # 1. 내용이 없으면(빈 칸) -> 계속 오른쪽으로 이동 (continue)
                if not val:
                    continue
                
                # 2. 내용이 있는데 '성명', '대표자', '등록번호' 같은 라벨이다? -> 찾기 실패 (break)
                if any(keyword in val for keyword in ["성명", "대표자", "등록번호", "사업자"]):
                    break
                
                # 3. 그 외의 내용이 있다면 -> 이것이 회사명입니다!
                # 괄호나 특수문자가 섞여 있을 수 있으니 정제합니다.
                회사명 = val.replace("(", "").replace(")", "").replace("법인명", "").strip()
                break # 형제 찾기 루프 종료
            
            if 회사명:
                break # 전체 루프 종료 (첫 번째 발견된 상호 사용)

        # -------------------------------------------------
        # [2] 정산일자 추출 (작성일자 라벨 검색 + 정규식 백업)
        # -------------------------------------------------
        # 방법 A: '작성'과 '일자'가 들어간 칸 옆에 있는 날짜 찾기
        date_cells = soup.find_all(lambda tag: tag.name in ['td', 'th'] and ('작성' in tag.get_text() and '일자' in tag.get_text()))
        for cell in date_cells:
            siblings = cell.find_next_siblings(['td', 'th'])
            for sibling in siblings:
                val = sibling.get_text(strip=True)
                # 날짜 형식(숫자로 시작)이 보이면 가져옵니다.
                if val and val[0].isdigit():
                    # 숫자만 남기고 추출
                    nums = re.findall(r'\d+', val)
                    if len(nums) >= 3: # 연, 월, 일
                        y = nums[0]
                        m = nums[1].zfill(2)
                        d = nums[2].zfill(2)
                        정산일자 = f"{y}{m}{d}"
                        break
            if 정산일자: break

        # 방법 B: 실패했다면 전체 텍스트에서 날짜 패턴 검색 (백업)
        if not 정산일자:
            text_content = soup.get_text()
            # 2023-12-31, 2023.12.31, 2023년 12월 31일 등 모두 대응
            date_pattern = r"(\d{4})[\s\.\-\년]+(\d{1,2})[\s\.\-\월]+(\d{1,2})[\s\.\-\일]*"
            matches = re.findall(date_pattern, text_content)
            if matches:
                # 가장 문서 상단에 있는 날짜가 작성일자일 확률이 높음
                y, m, d = matches[0]
                정산일자 = f"{y}{m.zfill(2)}{d.zfill(2)}"

        return 회사명.strip(), 정산일자

    except Exception as e:
        # 에러 발생 시 로그라도 남기면 좋습니다.
        print(f"HTML Parsing Error: {e}")
        return "", ""

# =====================================================
# 3. Selenium 설정 (기존 유지)
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
# 4. 앱 실행 로직
# =====================================================
st.set_page_config(page_title="Boosters Tax Converter", page_icon="📄")
user_email = require_login()

st.sidebar.success(f"✅ 로그인됨\n{user_email}")
if st.sidebar.button("로그아웃"):
    st.session_state.clear()
    st.rerun()

st.title("📄 세금계산서 PDF 변환기 (Boosters)")
st.info("💡 팁: HTML 파일을 업로드하면 '공급자'의 상호명을 자동으로 인식하여 파일명을 변경합니다.")

uploaded_files = st.file_uploader("HTML 파일 선택", type="html", accept_multiple_files=True)
biz_num = st.text_input("비밀번호 (사업자번호)", value="1828801269")

if st.button("🚀 변환 시작") and uploaded_files:
    driver = get_driver()
    
    for idx, f in enumerate(uploaded_files):
        with st.status(f"처리 중: {f.name}") as status:
            try:
                # 1. HTML 원본 읽기 및 인코딩 보정
                raw_bytes = f.getvalue()
                try:
                    html_content = raw_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        html_content = raw_bytes.decode('euc-kr')
                    except:
                        html_content = raw_bytes.decode('cp949', errors='ignore')

                # 2. [변경] PDF 변환 전에 HTML에서 정보(상호, 날짜)를 먼저 추출합니다.
                # PDF 텍스트보다 HTML 태그 구조가 훨씬 정확합니다.
                회사명, 정산일자 = extract_info_from_html_content(html_content)
                
                # 3. 폰트 강제 적용 스타일 삽입 (PDF 깨짐 방지용)
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
                    html_content_for_pdf = html_content.replace("<head>", "<head>" + font_style, 1)
                else:
                    html_content_for_pdf = font_style + html_content

                # 4. Selenium용 임시 파일 저장
                with tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode='w', encoding='utf-8') as tmp:
                    tmp.write(html_content_for_pdf)
                    h_path = tmp.name

                # 5. Selenium 실행
                driver.get(f"file://{h_path}")
                wait = WebDriverWait(driver, 10)
                
                try:
                    pw = wait.until(EC.presence_of_element_located((By.XPATH, '//input[@type="password"]')))
                    pw.send_keys(biz_num)
                    driver.find_element(By.XPATH, '//button[contains(text(),"확인")]').click()
                    time.sleep(5) 
                except:
                    pass 

                # 6. PDF 생성
                pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                    "printBackground": True,
                    "paperWidth": 8.27,
                    "paperHeight": 11.69
                })
                pdf_bytes = base64.b64decode(pdf_data["data"])
                
                # 7. 파일명 생성 (HTML에서 추출한 정확한 정보 사용)
                if not 회사명: 회사명 = "상호미상"
                safe_name = re.sub(r'[\\/*?:"<>|]', "_", 회사명)
                fn = f"세금계산서_{safe_name}_{정산일자}.pdf" if 정산일자 else f"세금계산서_{safe_name}_{int(time.time())}.pdf"
                
                # 8. 다운로드 버튼
                st.download_button(label=f"📥 {fn}", data=pdf_bytes, file_name=fn, mime="application/pdf", key=f"d_{idx}")
                status.update(label=f"✅ 완료: {fn}", state="complete")
                
                os.unlink(h_path)
                
            except Exception as e:
                st.error(f"오류: {str(e)}")
                
    driver.quit()
    st.balloons()
