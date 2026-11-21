"""One bounded trusted local computation. The parent owns all database and signing operations."""
import sys

def main():
    import os,resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_FSIZE,(8<<20,8<<20))
    resource.setrlimit(resource.RLIMIT_CPU,(40,45))
    # macOS can impose a lower hard address-space limit; process supervision still bounds wall time.
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS,(2<<30,2<<30))
    from .core.contract import canonical,strict_load,require,Refused
    try:
        b=sys.stdin.buffer.read((5<<20)+1);require(len(b)<=5<<20,"INPUT_LIMIT","Oversized worker input")
        x=strict_load(b);kind=x["kind"]
        if kind=="sanad":from .sanad.engine import evaluate
        elif kind=="basira":from .basira.engine import evaluate
        elif kind=="rasid":from .rasid.engine import evaluate
        else:raise Refused("KIND","Unknown product")
        result=evaluate(x["payload"],x["profile"])
        out=canonical({"ok":True,"value":result})
    except (Refused,MemoryError,ValueError,KeyError,TypeError,IndexError,OverflowError,ArithmeticError) as exc:
        code=exc.code if isinstance(exc,Refused) else "MEMORY" if isinstance(exc,MemoryError) else "INVALID_INPUT"
        out=canonical({"ok":False,"code":code,"detail":str(exc)[:180] if isinstance(exc,Refused) else "Input or numerical contract failed"})
    sys.stdout.buffer.write(out)

if __name__=="__main__":main()
