"""Desktop launcher; --cli exposes the same pipeline for automation."""
import argparse
import contextlib
import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
import pmxconv as core


class QueueWriter:
    def __init__(self, events): self.events=events
    def write(self, value):
        if value: self.events.put(('log', value))
    def flush(self): pass


class App:
    def __init__(self, root):
        self.root=root; self.events=queue.Queue(); self.busy=False; self.last_package=''
        self.runtime=None
        root.title('pmxconv 0.7.2 • PMX → RMDL');root.geometry('900x740');root.minsize(720,680)
        style=ttk.Style();style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10))
        style.configure('TButton',padding=(12,7));style.configure('TEntry',padding=5)
        outer=ttk.Frame(root,padding=18);outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='pmxconv',font=('Segoe UI',22,'bold')).pack(anchor='w')
        self.values={}
        defaults={'input':'','name':'model','output':str(Path(sys.executable).parent/'converted' if getattr(sys,'frozen',False) else core.ROOT/'outputs/converted'), 'game':str(core.R5F) if str(core.R5F) != '.' else '', 'tools':str(core.TOOLS) if str(core.TOOLS) != '.' else '', 'blender':str(core.BLENDER) if str(core.BLENDER) != '.' else '', 'sfm':str(core.SFM_BIN.parent) if str(core.SFM_BIN.parent) != '.' else ''}
        self.settings=Path(sys.executable).parent/'pmxconv-settings.json' if getattr(sys,'frozen',False) else core.ROOT/'work/pmxconv-settings.json'
        if self.settings.exists():
            try: defaults.update(json.loads(self.settings.read_text(encoding='utf-8')))
            except (OSError,ValueError): pass
        fields=ttk.Frame(outer);fields.pack(fill='x',pady=(10,12));fields.columnconfigure(1,weight=1)
        for row,(key,label,kind) in enumerate([('input','PMX 模型','file'),('name','模型标识',''),('output','输出目录','dir'),('game','R5F 目录','dir'),('tools','转换工具目录','dir'),('blender','Blender 程序','file'),('sfm','SourceFilmmaker game 目录','dir')]):
            ttk.Label(fields,text=label).grid(row=row,column=0,sticky='w',padx=(0,14),pady=4)
            var=tk.StringVar(value=defaults[key]);self.values[key]=var
            ttk.Entry(fields,textvariable=var).grid(row=row,column=1,sticky='ew',pady=4)
            if kind:ttk.Button(fields,text='…',width=3,command=lambda k=key,t=kind:self.browse(k,t)).grid(row=row,column=2,padx=(6,0))
        opts=ttk.Frame(outer);opts.pack(fill='x')
        self.install=tk.BooleanVar(value=True);self.strict=tk.BooleanVar(value=False)
        ttk.Checkbutton(opts,text='转换后安装到 R5F',variable=self.install).pack(side='left')
        ttk.Checkbutton(opts,text='严格骨骼模式',variable=self.strict).pack(side='left',padx=18)
        ttk.Label(opts,text='摆动骨上限').pack(side='left')
        self.max_jiggle=tk.IntVar(value=128)
        ttk.Spinbox(opts,from_=0,to=189,textvariable=self.max_jiggle,width=5).pack(side='left',padx=6)
        effects=ttk.Frame(outer);effects.pack(fill='x',pady=8)
        self.first_person=tk.BooleanVar(value=True)
        ttk.Checkbutton(effects,text='生成无头第一人称手臂和身体',variable=self.first_person).pack(side='left')
        self.outline_width=tk.DoubleVar(value=0);self.fp_outline_width=tk.DoubleVar(value=0);self.skin_emission=tk.DoubleVar(value=0)
        for label,value in [('人物描边',self.outline_width),('手臂描边',self.fp_outline_width),('皮肤自发光',self.skin_emission)]:
            ttk.Label(effects,text=label).pack(side='left',padx=(12,4))
            ttk.Spinbox(effects,from_=0,to=1,increment=.01,textvariable=value,width=5).pack(side='left')
        animation_row=ttk.Frame(outer);animation_row.pack(fill='x',pady=(0,4))
        ttk.Label(animation_row,text='站立/奔跑动画').pack(side='left',padx=(0,4))
        self.animation_style=tk.StringVar(value='loba')
        ttk.Combobox(animation_row,textvariable=self.animation_style,values=list(core.BODY_REFERENCES),state='readonly',width=8).pack(side='left')
        self.actions=ttk.Frame(outer);self.actions.pack(fill='x',pady=12)
        self.convert_button=ttk.Button(self.actions,text='转换 S21 + S3',command=self.convert);self.convert_button.pack(side='left')
        self.test_button=ttk.Button(self.actions,text='第三人称测试',command=self.test);self.test_button.pack(side='left',padx=8)
        self.install_button=ttk.Button(self.actions,text='安装已有包',command=self.install_existing);self.install_button.pack(side='left')
        self.cancel_button=ttk.Button(self.actions,text='取消转换',command=core.CANCEL.set,state='disabled');self.cancel_button.pack(side='right')
        self.status=tk.StringVar(value='就绪');ttk.Label(outer,textvariable=self.status).pack(anchor='w')
        self.progress=ttk.Progressbar(outer,mode='indeterminate');self.progress.pack(fill='x',pady=6)
        logframe=ttk.Frame(outer);logframe.pack(fill='both',expand=True)
        self.log=tk.Text(logframe,wrap='word',background='#17221e',foreground='#dce8df',font=('Consolas',10),relief='flat',padx=10,pady=10,state='disabled')
        self.log.pack(side='left',fill='both',expand=True);scroll=ttk.Scrollbar(logframe,command=self.log.yview);scroll.pack(side='right',fill='y');self.log.configure(yscrollcommand=scroll.set)
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(100,self.poll)

    def browse(self,key,kind):
        path=filedialog.askdirectory() if kind=='dir' else filedialog.askopenfilename(filetypes=[('PMX','*.pmx'),('All','*.*')] if key=='input' else [('Executable','*.exe')])
        if path:self.values[key].set(path)

    def launch(self,fn,status,cancellable=False):
        if self.busy:return
        self.busy=True;self.status.set(status);self.progress.start()
        for b in [self.convert_button,self.test_button,self.install_button]:b.configure(state='disabled')
        self.cancel_button.configure(state='normal' if cancellable else 'disabled')
        def worker():
            try:
                with contextlib.redirect_stdout(QueueWriter(self.events)),contextlib.redirect_stderr(QueueWriter(self.events)):
                    fn()
                self.events.put(('done','完成'))
            except Exception as exc:self.events.put(('error',str(exc)))
        threading.Thread(target=worker,daemon=False).start()

    def convert(self):
        values={k:v.get().strip() for k,v in self.values.items()}
        self.settings.parent.mkdir(parents=True,exist_ok=True);self.settings.write_text(json.dumps(values,ensure_ascii=False,indent=2),encoding='utf-8')
        args=argparse.Namespace(**values,reference='',strict=self.strict.get(),install=self.install.get(),max_jiggle=self.max_jiggle.get(),first_person=self.first_person.get(),outline_width=self.outline_width.get(),fp_outline_width=self.fp_outline_width.get(),skin_emission=self.skin_emission.get(),animation_style=self.animation_style.get())
        def run():
            package,work,report=core.convert(args);self.last_package=str(package)
            print('Report:',package/'PMXConv-report.json')
        self.launch(run,'正在转换',True)

    def install_existing(self):
        path=filedialog.askdirectory(title='选择 R5F-PMXConv-* 包目录')
        if path:
            game=self.values['game'].get()
            self.launch(lambda:core.integration.install(core,path,game),'正在安装')

    def test(self):
        game=self.values['game'].get();out=Path(self.values['output'].get())/'runtime'
        def run():
            out.mkdir(parents=True,exist_ok=True)
            cmd=['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(core.RESOURCES/'runtime_test.ps1'),'-GameRoot',game,'-ReportRoot',str(out)]
            self.runtime=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)
            for line in self.runtime.stdout:print(line,end='')
            rc=self.runtime.wait();self.runtime=None
            if rc:raise RuntimeError('Runtime runner failed')
            report=json.loads((out/'report.json').read_text(encoding='utf-8-sig'))
            if not report.get('completed'):raise RuntimeError(report.get('error','Runtime incomplete'))
            from runtime_audit import audit
            report=audit(out)
            if not report['audit']['passed']:raise RuntimeError('游戏测试未通过；检查 runtime/report.json 中的骨骼和动作证据')
            print('Runtime evidence:',out)
        self.launch(run,'正在运行第三人称测试')

    def poll(self):
        try:
            while True:
                kind,value=self.events.get_nowait()
                self.log.configure(state='normal');self.log.insert('end',value if kind=='log' else '\n'+value+'\n');self.log.see('end');self.log.configure(state='disabled')
                if kind!='log':
                    self.busy=False;self.progress.stop();self.status.set('失败' if kind=='error' else value)
                    for b in [self.convert_button,self.test_button,self.install_button]:b.configure(state='normal')
                    self.cancel_button.configure(state='disabled')
        except queue.Empty:pass
        self.root.after(100,self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo('pmxconv','请先取消转换，或等待测试恢复游戏文件后再退出。')
            return
        self.root.destroy()


if __name__=='__main__':
    if '--cli' in sys.argv:
        sys.argv.remove('--cli');core.main()
    else:
        root=tk.Tk();App(root);root.mainloop()
