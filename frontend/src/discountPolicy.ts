export type DiscountVersion = { day: string; factor: string };
export type DiscountUse = { factor: string | null; versionDay: string | null; mode: "historical" | "fallback" | "missing" };
const dayMs = 86400000;
const iso = (stamp: number) => new Date(stamp).toISOString().slice(0,10);
function stamp(day: string): number | null {
  if(!/^\d{4}-\d{2}-\d{2}$/.test(day))return null;
  const value=Date.parse(day+"T00:00:00Z");
  return Number.isFinite(value)&&iso(value)===day?value:null;
}
function useOnDay(versions:DiscountVersion[], day:string, today:string):DiscountUse {
  const historic=versions.filter(v=>v.day<=day).at(-1);
  if(historic)return {factor:historic.factor,versionDay:historic.day,mode:"historical"};
  const current=versions.filter(v=>v.day<=today).at(-1);
  return current?{factor:current.factor,versionDay:current.day,mode:"fallback"}:{factor:null,versionDay:null,mode:"missing"};
}
const signature=(use:DiscountUse)=>[use.mode,use.versionDay,use.factor===null?null:Number(use.factor)].join("|");
export function previewDiscount(versions:DiscountVersion[],factor:string,effective:string,start:string,end:string,today=new Date().toISOString().slice(0,10)) {
  const ordered=[...versions].sort((a,b)=>a.day.localeCompare(b.day));
  const effectiveStamp=stamp(effective),startStamp=stamp(start),endStamp=stamp(end);
  const inputValid=factor.trim()!==""&&Number.isFinite(Number(factor))&&Number(factor)>=0&&Number(factor)<=1&&effectiveStamp!==null;
  const conflict=ordered.find(v=>v.day===effective);
  const valid=inputValid&&!conflict;
  const after=valid?[...ordered,{day:effective,factor}].sort((a,b)=>a.day.localeCompare(b.day)):ordered;
  const next=ordered.find(v=>v.day>effective);
  const until=next&&effectiveStamp!==null?iso(stamp(next.day)!-dayMs):null;
  const rows:{from:string;to:string;days:number;before:DiscountUse;after:DiscountUse;changed:boolean}[]=[];
  const rangeValid=startStamp!==null&&endStamp!==null&&endStamp>=startStamp&&(endStamp-startStamp)/dayMs<=366;
  let changedDays=0;
  if(rangeValid)for(let cursor=startStamp!;cursor<=endStamp!;cursor+=dayMs){
    const day=iso(cursor),before=useOnDay(ordered,day,today),now=useOnDay(after,day,today);
    const changed=signature(before)!==signature(now);if(changed)changedDays++;
    const previous=rows.at(-1);
    if(previous&&signature(previous.before)===signature(before)&&signature(previous.after)===signature(now)){
      previous.to=day;previous.days++;
    }else rows.push({from:day,to:day,days:1,before,after:now,changed});
  }
  const otherFactors=valid&&rows.some(r=>r.after.factor!==null&&Number(r.after.factor)!==Number(factor));
  return {valid,inputValid,conflict,until,rows,rangeValid,changedDays,otherFactors,currentBefore:useOnDay(ordered,today,today),currentAfter:useOnDay(after,today,today)};
}
