/* Particles */
(function(){
    var c=document.getElementById('particles');
    if(!c)return;
    for(var i=0;i<40;i++){
        var p=document.createElement('div');
        p.className='particle';
        p.style.left=Math.random()*100+'%';
        p.style.animationDuration=(8+Math.random()*12)+'s';
        p.style.animationDelay=(Math.random()*10)+'s';
        p.style.width=p.style.height=(1+Math.random()*3)+'px';
        c.appendChild(p);
    }
})();

/* Model chip select */
document.querySelectorAll('.chip').forEach(function(c){
    c.addEventListener('click',function(){
        document.querySelectorAll('.chip').forEach(function(x){x.classList.remove('active')});
        c.classList.add('active');
    });
});

/* Demo prediction with 3-try limit */
var demoUses = parseInt(localStorage.getItem('demo_uses') || '0');

function runDemo(){
    var text = document.getElementById('demoText').value.trim();
    if(!text){return;}

    if(demoUses >= 3){
        document.getElementById('demoResult').classList.add('show');
        document.getElementById('resBadge').textContent = 'Limit Reached';
        document.getElementById('resBadge').className = 'result-badge misleading';
        document.getElementById('resConf').textContent = '';
        document.getElementById('resBars').innerHTML = '';
        document.getElementById('resExplain').textContent = 'You have used all 3 free demos. Please log in or register to continue using the detector.';
        document.getElementById('resClaim').innerHTML = '';
        document.getElementById('resSimilar').innerHTML = '';
        return;
    }

    var model = document.querySelector('.chip.active').dataset.model;
    var btn = document.getElementById('analyzeBtn');
    btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Analyzing...';

    fetch('/api/demo-predict',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:text,model:model})})
    .then(function(r){if(!r.ok){return r.json().then(function(d){throw new Error(d.error||'Request failed');});}return r.json();})
    .then(function(d){
        btn.disabled = false; btn.innerHTML = '<i class="fas fa-magnifying-glass"></i> Analyze';
        if(d.error){alert(d.error);return;}

        demoUses++;
        localStorage.setItem('demo_uses', demoUses);

        var res = document.getElementById('demoResult');

        if(d.not_political){
            var badge = document.getElementById('resBadge');
            badge.textContent = 'Not Political';
            badge.className = 'result-badge';
            badge.style.background = '#64748b';
            document.getElementById('resConf').textContent = '0%';
            document.getElementById('resBars').innerHTML = '<div style="padding:16px;text-align:center;"><p style="font-size:14px;color:var(--text-dim);"><i class="fas fa-info-circle" style="color:#64748b;"></i> '+d.analysis+'</p></div>';
            document.getElementById('resClaim').innerHTML = '';
            document.getElementById('resSimilar').innerHTML = '';
            res.classList.add('show');
            return;
        }
        var badge = document.getElementById('resBadge');
        badge.textContent = d.prediction;
        badge.className = 'result-badge ' + d.prediction.toLowerCase();
        document.getElementById('resConf').textContent = d.confidence + '%';

        /* Single confidence bar */
        var barColor = d.prediction === 'Reliable' ? 'green' : d.prediction === 'Misinformation' ? 'red' : 'yellow';
        document.getElementById('resBars').innerHTML = '<div class="r-bar"><div class="bar-head"><span class="bar-label '+barColor+'">'+d.prediction+'</span><span class="bar-val">'+d.confidence+'%</span></div><div class="bar-track"><div class="bar-fill '+barColor+'" style="width:'+d.confidence+'%"></div></div></div>';

        var explain = '';
        if(d.prediction === 'Reliable'){
            explain = 'Qoraalkani wuu u muuqdaa mid lagu kalsooni qabo.';
        } else if(d.prediction === 'Misinformation'){
            explain = 'Qoraalkani waa mid been abuur ah oo su\'aal laga qabo.';
        } else {
            explain = 'Qoraalkani waa mid la marin habaabi karo.';
        }
        document.getElementById('resExplain').textContent = explain;

        /* Main Claim */
        var claimEl = document.getElementById('resClaim');
        if(d.main_claim){
            claimEl.innerHTML = '<div style="margin-top:20px;padding:16px;background:var(--input-bg);border:1px solid var(--border);border-radius:12px;"><p style="font-size:13px;color:var(--text-dim);margin-bottom:6px;font-weight:600;"><i class="fas fa-bullseye" style="color:var(--primary-light);"></i> Main Claim</p><p style="font-size:14px;color:var(--text);margin:0;line-height:1.6;">'+d.main_claim+'</p></div>';
        } else {
            claimEl.innerHTML = '';
        }

        /* Similar Content or Reported By */
        var simEl = document.getElementById('resSimilar');

        if(d.reported_by && d.reported_by.length > 0){
            var html = '<div style="margin-top:20px;"><p style="font-size:13px;color:var(--text-dim);margin-bottom:10px;font-weight:600;"><i class="fas fa-check-circle" style="color:var(--success);"></i> Also Reported By ('+d.reported_by.length+')</p>';
            d.reported_by.forEach(function(src){
                html += '<div style="display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--border);">';
                html += '<div style="flex:1;min-width:0;"><span style="font-size:14px;font-weight:700;color:var(--success);"><i class="fas fa-check-circle"></i></span> ';
                html += '<span style="font-size:13px;color:var(--text);font-weight:600;">'+src.name+'</span> ';
                html += '<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(16,185,129,0.12);color:var(--success);">'+src.type+'</span> ';
                html += '<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(99,102,241,0.08);color:var(--text-dim);">'+src.credibility+'</span>';
                if(src.first_result){
                    html += '<p style="font-size:12px;color:var(--text-muted);margin:2px 0 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'+src.first_result.title+'</p>';
                }
                html += '</div>';
                html += '<a href="'+src.url+'" target="_blank" rel="noopener" style="font-size:11px;padding:4px 10px;border:1px solid var(--border);border-radius:6px;color:var(--text-muted);text-decoration:none;flex-shrink:0;margin-left:10px;">Visit</a></div>';
            });
            html += '</div>';
            simEl.innerHTML = html;
        } else if(d.similar_content && d.similar_content.length > 0){
            var html = '<div style="margin-top:20px;"><p style="font-size:13px;color:var(--text-dim);margin-bottom:10px;font-weight:600;"><i class="fas fa-globe" style="color:var(--primary-light);"></i> Similar Content ('+d.similar_content.length+')</p>';
            d.similar_content.forEach(function(item){
                var matchColor = item.similarity >= 80 ? 'var(--success)' : item.similarity >= 50 ? 'var(--warning)' : 'var(--text-dim)';
                html += '<div style="display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--border);">';
                html += '<div style="flex:1;min-width:0;"><span style="font-size:11px;font-weight:700;padding:2px 6px;border-radius:4px;background:rgba(99,102,241,0.1);color:var(--primary);">'+item.platform+'</span> ';
                html += '<span style="font-size:13px;color:var(--text);font-weight:600;">'+item.source+'</span>';
                html += '<p style="font-size:12px;color:var(--text-muted);margin:2px 0 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'+item.title+'</p></div>';
                html += '<div style="display:flex;align-items:center;gap:10px;flex-shrink:0;margin-left:10px;">';
                html += '<span style="font-size:14px;font-weight:800;color:'+matchColor+';">'+item.similarity+'%</span>';
                html += '<a href="'+item.url+'" target="_blank" rel="noopener" style="font-size:11px;padding:4px 10px;border:1px solid var(--border);border-radius:6px;color:var(--text-muted);text-decoration:none;">Open</a></div></div>';
            });
            html += '</div>';
            simEl.innerHTML = html;
        } else {
            simEl.innerHTML = '<div style="margin-top:20px;text-align:center;padding:20px;"><p style="font-size:13px;color:var(--text-dim);"><i class="fas fa-search"></i> No Results Found</p></div>';
        }

        res.classList.add('show');

        if(demoUses >= 3){
            setTimeout(function(){
                var link = document.createElement('p');
                link.innerHTML = '<a href="/register" style="color:var(--primary-light);font-weight:600;text-decoration:underline;">Register free</a> to get unlimited predictions.';
                link.style.marginTop = '12px';
                link.style.fontSize = '14px';
                document.getElementById('demoResult').appendChild(link);
            }, 500);
        }
    })
    .catch(function(e){
        btn.disabled = false; btn.innerHTML = '<i class="fas fa-magnifying-glass"></i> Analyze';
        alert('Error: ' + e.message);
    });
}
