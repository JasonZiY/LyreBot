# Draws assets/lyrebot.png and lyrebot.ico. Needs Pillow; run from the project folder.
from PIL import Image, ImageDraw
SS=4; S=1024*SS
im=Image.new("RGBA",(S,S),(0,0,0,0)); d=ImageDraw.Draw(im)
k=lambda *v:[x*SS for x in v]
d.rounded_rectangle(k(40,40,984,984),radius=230*SS,fill=(217,119,66,255))
W=(255,247,236,255)
def bez(p,n=200):
    out=[]
    for i in range(n+1):
        t=i/n; pts=p
        while len(pts)>1:
            pts=[(a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t) for a,b in zip(pts,pts[1:])]
        out.append(pts[0])
    return out
def stroke(pts,w):
    r=w/2
    for x,y in pts: d.ellipse(k(x-r,y-r,x+r,y+r),fill=W)
left=bez([(420,800),(200,760),(170,450),(330,330),(400,280),(340,170),(250,200)])
stroke(left,64)
stroke([(1024-x,y) for x,y in left],64)
# horn knobs
for x in (250,774): d.ellipse(k(x-44,200-44,x+44,200+44),fill=W)
# crossbar
d.rounded_rectangle(k(300,300,724,350),radius=25*SS,fill=W)
# base
d.rounded_rectangle(k(360,770,664,840),radius=35*SS,fill=W)
for x in (434,486,538,590):
    d.rounded_rectangle(k(x-11,330,x+11,790),radius=11*SS,fill=W)
im=im.resize((1024,1024),Image.LANCZOS)
im.save("assets/lyrebot.png")
# One frame per size Windows asks for at 100%-200% scaling, each scaled straight
# from the big image, so Windows never has to stretch a frame (that blurs it).
SIZES=[16,20,24,30,32,36,40,48,60,64,72,80,96,128,256]
frames=[im.resize((n,n),Image.LANCZOS) for n in SIZES]
frames[-1].save("assets/lyrebot.ico",sizes=[(n,n) for n in SIZES],append_images=frames[:-1])
