"""全站共享主题：CSS 部分（毛玻璃/光晕/品牌字型等 config.toml 做不到的）。

设计语言：象牙白底 #FFFEF8，青 #0F7B72 × 青铜 #9A6B2F，毛玻璃卡片
签名元素：贯穿顶部的青→青铜渐变细线（呼应 jnove.dpdns.org）；
衬线品牌标题 + 青铜斜体 's；Vivia 式竖条小节标题；
毛玻璃配方参考 silhouette.dpdns.org：blur(18px) saturate(1.65) + 半透明白底 + 白描边 + 彩色柔影。

颜色/圆角/字号等原生主题项在 .streamlit/config.toml，改配色两边要同步。
app.py 每轮 rerun 调一次 apply_theme()，登录页/问答页/管理页共用。
"""
import streamlit as st

THEME_CSS = """
<style>
@import url('https://fonts.loli.net/css2?family=Noto+Serif+SC:wght@700;900&display=swap');

/* header 保留（侧边栏收起后的展开按钮在里面），只做透明化；
   彩虹装饰条隐藏，顶部细线由 .top-thread 提供 */
header[data-testid="stHeader"] { background: transparent; }
[data-testid="stDecoration"] { display: none; }

.stApp { background: #FFFEF8; }
/* 垫在毛玻璃后面的两团光晕：青（右上）+ 青铜（左下），没有它们玻璃糊不出层次 */
.stApp::before {
  content: ''; position: fixed; inset: 0; pointer-events: none;
  background:
    radial-gradient(ellipse 42% 38% at 82% 8%, rgba(15,123,114,.16), transparent 70%),
    radial-gradient(ellipse 40% 36% at 10% 88%, rgba(154,107,47,.14), transparent 70%),
    radial-gradient(ellipse 30% 26% at 45% 55%, rgba(15,123,114,.05), transparent 70%);
}
/* header 现在可见（fixed 约 3.75rem），顶部留白比隐藏时代多一点 */
.block-container { padding: 4.2rem 2.4rem 0.8rem; max-width: 1240px; }

.top-thread {
  position: fixed; top: 0; left: 0; right: 0; height: 3px; z-index: 999999;
  pointer-events: none;
  background: linear-gradient(90deg, #0F7B72 0%, #6FAE9B 35%, #C9A46B 65%, #9A6B2F 100%);
}

.eyebrow {
  letter-spacing: .35em; color: #0F7B72; font-size: .72rem; font-weight: 700;
  text-transform: uppercase; margin-bottom: .15rem;
}
.brand {
  font-family: Georgia, 'Noto Serif SC', 'Source Han Serif SC', serif;
  font-size: 2.05rem; font-weight: 900; color: #22332F; line-height: 1.15;
}
.brand .apo { font-style: italic; color: #9A6B2F; font-weight: 700; }
.brand-sub { color: #8C948F; font-size: .88rem; margin-top: .2rem; }
.brand-rule {
  width: 72px; height: 3px; border-radius: 2px; margin: .7rem 0 1rem;
  background: linear-gradient(90deg, #0F7B72, #9A6B2F);
}

.panel-title {
  display: flex; align-items: center; gap: .55rem;
  font-size: 1.02rem; font-weight: 700; color: #22332F; margin: .1rem 0 .55rem;
}
.panel-title .bar { width: 4px; height: 1.05em; border-radius: 2px; }
.bar-teal { background: #0F7B72; }
.bar-bronze { background: #9A6B2F; }
.count-pill {
  background: rgba(154,107,47,.14); color: #7F5624; border-radius: 999px;
  font-size: .72rem; font-weight: 700; padding: .05rem .55rem;
}

/* 毛玻璃卡片（笔记面板 / 聊天面板 / 登录卡片）；st.container(key=...) 生成的稳定 class */
.st-key-chat_box, .st-key-notes_box, .st-key-auth_box {
  background: rgba(255,255,255,.62);
  backdrop-filter: blur(18px) saturate(1.65);
  -webkit-backdrop-filter: blur(18px) saturate(1.65);
  border: 1px solid rgba(255,255,255,.78); border-radius: 18px;
  box-shadow: rgba(15,123,114,.10) 0 24px 56px, rgba(30,41,36,.06) 0 3px 12px;
}

/* —— 登录页 —— */
.auth-hero { text-align: center; margin: 1.6rem 0 1.3rem; }
.auth-hero .brand-rule { margin: .7rem auto 0; }
.st-key-auth_box { padding: 1.15rem 1.35rem 1.35rem; }
.st-key-auth_box [data-testid="stForm"] { border: none; padding: 0; }

.note-card {
  background: rgba(255,255,254,.72); border: 1px solid rgba(255,255,255,.85);
  border-left: 3px solid #0F7B72;
  border-radius: 14px; padding: .75rem .95rem .65rem; margin-bottom: .7rem;
  box-shadow: rgba(30,41,36,.05) 0 2px 8px;
  transition: transform .18s ease, box-shadow .18s ease;
}
.note-card:hover { transform: translateY(-1px); box-shadow: rgba(154,107,47,.14) 0 6px 18px; }
.note-q { font-weight: 700; color: #22332F; margin-bottom: .35rem; }
.note-points { margin: 0 0 .45rem 1.1rem; padding: 0; color: #3E4A49; font-size: .9rem; }
.note-points li { margin-bottom: .15rem; }
.note-src {
  font-size: .78rem; color: #9AA39D; margin-top: .4rem;
  display: flex; flex-direction: column; gap: .2rem;
}
.note-src-item { line-height: 1.35; }
.note-src-item::before { content: "·"; color: #C9A46B; margin-right: .38rem; font-weight: 700; }
.note-src a { color: #8C6428; text-decoration: none; }
.note-src a:hover { text-decoration: underline; }

.note-empty { position: relative; text-align: center; padding: 2.4rem 1rem; }
.note-empty .ghost {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  font-family: Georgia, 'Noto Serif SC', serif; font-size: 6.5rem; font-weight: 900;
  color: rgba(154,107,47,.10); user-select: none; pointer-events: none;
}
.note-empty .hint { position: relative; z-index: 1; color: #9AA39D; font-size: .88rem; margin-top: 3.6rem; }

[data-testid="stChatMessage"] {
  background: rgba(255,255,254,.72); border: 1px solid rgba(255,255,255,.85);
  border-radius: 16px; padding: .6rem .8rem;
  box-shadow: rgba(30,41,36,.04) 0 2px 8px;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  background: rgba(15,123,114,.10); border-color: rgba(15,123,114,.12);
}

[data-testid="stChatInput"] {
  background: rgba(255,255,255,.70);
  backdrop-filter: blur(18px) saturate(1.65);
  -webkit-backdrop-filter: blur(18px) saturate(1.65);
  border: 1.5px solid rgba(255,255,255,.8); border-radius: 999px;
  box-shadow: rgba(30,41,36,.07) 0 2px 12px;
}
[data-testid="stChatInput"]:focus-within { border-color: #0F7B72; }
[data-testid="stChatInput"] > div { background: transparent; border: none; }

.stDownloadButton button {
  background: #9A6B2F !important; color: #fff !important; border: none !important;
  border-radius: 999px !important; font-weight: 700;
  box-shadow: rgba(154,107,47,.35) 0 2px 8px;
}
.stDownloadButton button:hover { background: #7F5624 !important; }

div[data-testid="stExpander"] details {
  background: rgba(255,255,254,.6); border: 1px solid rgba(154,107,47,.14); border-radius: 12px;
}

*::-webkit-scrollbar { width: 8px; height: 8px; }
*::-webkit-scrollbar-thumb { background: #E3DCC8; border-radius: 4px; }
*::-webkit-scrollbar-track { background: transparent; }
</style>
"""


def apply_theme() -> None:
    """注入共享 CSS 和顶部渐变细线；app.py 每轮调用一次，所有页面生效。"""
    st.markdown(THEME_CSS + '<div class="top-thread"></div>', unsafe_allow_html=True)
