import re
from datetime import datetime,timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from config import IPOJI_BASE_URL,IPOJI_CURRENT_IPO_URL,REQUEST_TIMEOUT
from database import Database
HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139.0 Safari/537.36','Accept-Language':'en-US,en;q=0.9'}
class IPOJiClient:
 def __init__(self): self.s=requests.Session(); self.s.headers.update(HEADERS)
 def get_open_ipos(self):
  url=urljoin(IPOJI_BASE_URL,IPOJI_CURRENT_IPO_URL); r=self.s.get(url,timeout=REQUEST_TIMEOUT); r.raise_for_status(); return self._parse(BeautifulSoup(r.text,'lxml'),r.url)
 def _parse(self,soup,page_url):
  now=datetime.now(timezone.utc).isoformat(); rows=[]; seen=set()
  for a in soup.find_all('a',href=True):
   href=a['href']; url=urljoin(page_url,href); text=' '.join(a.stripped_strings)
   if '/ipo/' not in href or url in seen or not text: continue
   if not any(x in text.lower() for x in ('offer price','lot size','issue size')): continue
   row=self._card(text,url,now)
   if row and row['company_name']: seen.add(url); rows.append(row)
  return rows
 def _card(self,text,url,now):
  lines=[x.strip() for x in text.splitlines() if x.strip()]; company=lines[0] if lines else None
  dates=re.search(r'([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s*[–-]\s*([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})',text)
  price=re.search(r'Offer Price\s+₹\s*([\d,]+(?:\.\d+)?)\s*(?:-\s*([\d,]+(?:\.\d+)?))?',text,re.I)
  lot=re.search(r'Lot Size\s+([\d,]+)',text,re.I); issue=re.search(r'Issue Size\s+₹\s*([\d,.]+)\s*Cr',text,re.I); sub=re.search(r'Subscription\s+([\d,.]+)x',text,re.I); seg=re.search(r'(Mainboard|BSE SME|NSE SME|SME)',text,re.I); status=re.search(r'(Live|Open|Allotment Out|Allotment Awaited|Tentative Dates|DRHP Approved)',text,re.I)
  return {'source':'ipoji','source_id':url.rstrip('/').split('/')[-1],'company_name':company,'symbol':None,'ipo_type':'IPO','segment':seg.group(1) if seg else None,'status':status.group(1) if status else None,'open_date':dates.group(1) if dates else None,'close_date':dates.group(2) if dates else None,'listing_date':None,'price_low':num(price.group(1)) if price else None,'price_high':num(price.group(2) or price.group(1)) if price else None,'lot_size':num(lot.group(1)) if lot else None,'issue_size':num(issue.group(1)) if issue else None,'gmp':None,'subscription':num(sub.group(1)) if sub else None,'source_url':url,'collected_at':now,'raw':{'text':text}}
def num(v):
 c=re.sub(r'[^0-9.\-]','',str(v))
 try:return float(c) if '.' in c else int(c)
 except:return None
def collect_once():
 rows=IPOJiClient().get_open_ipos(); db=Database(); n=db.upsert_ipos(rows); db.close(); return {'count':n}
if __name__=='__main__': print(f"Collected and stored {collect_once()['count']} IPOs.")
