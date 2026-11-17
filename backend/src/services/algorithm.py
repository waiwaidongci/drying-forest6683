import hashlib,json,pickle
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split,KFold,cross_val_score
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error,mean_squared_error
from .domain_validation import validate_parameters
FEATURES=['moisture','thickness','temperature','airflow']
SUBSETS=[['moisture','temperature'],['moisture','thickness','temperature'],FEATURES]
BLIND_SEED=42
def _matrix(records):
    X=np.array([[r['values'][f] for f in FEATURES] for r in records],float)
    y=np.array([r['values']['minutes'] for r in records],float)
    return X,y
def fit_blind_model(records,parameters):
    # 盲测冻结模型：特征子集选择只在训练行内做3折CV，不接触任何盲测标签；最终森林用全部训练行拟合
    p=validate_parameters(parameters)
    if len(records)<40:raise ValueError('至少40条独立工艺样本，才能冻结盲测模型')
    X,y=_matrix(records)
    cv=KFold(3,shuffle=True,random_state=BLIND_SEED);candidates=[]
    for subset in SUBSETS:
        cols=[FEATURES.index(f) for f in subset]
        model=RandomForestRegressor(n_estimators=p['trees'],max_depth=p['depth'],random_state=BLIND_SEED,n_jobs=1)
        errors=-cross_val_score(model,X[:,cols],y,cv=cv,scoring='neg_mean_absolute_error',n_jobs=1)
        candidates.append({'features':subset,'cv_mae':float(errors.mean()),'fold_mae':errors.tolist()})
    best=min(candidates,key=lambda c:c['cv_mae'])
    cols=[FEATURES.index(f) for f in best['features']]
    model=RandomForestRegressor(n_estimators=p['trees'],max_depth=p['depth'],random_state=BLIND_SEED,n_jobs=1).fit(X[:,cols],y)
    training_checksum=hashlib.sha256(X.tobytes()+y.tobytes()).hexdigest()
    model_checksum=hashlib.sha256(json.dumps({'parameters':p,'features':best['features'],'training_checksum':training_checksum,'seed':BLIND_SEED},sort_keys=True).encode()).hexdigest()
    return {'model':model,'features':best['features'],'candidates':candidates,'training_checksum':training_checksum,'model_checksum':model_checksum,'training_rows':len(records),'pickle':pickle.dumps(model)}
def predict_blind(bundle,inputs):
    features=bundle['features'];cols=[FEATURES.index(f) for f in features]
    Xb=np.array([[r['values'][f] for f in FEATURES] for r in inputs],float)
    pred=bundle['model'].predict(Xb[:,cols])
    rows=[{'reference':inputs[i]['reference'],'predicted_minutes':float(v)} for i,v in enumerate(pred)]
    predictions_checksum=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
    return rows,predictions_checksum
def unblind_metrics(rows):
    actual=np.array([r['actual_minutes'] for r in rows],float)
    predicted=np.array([r['predicted_minutes'] for r in rows],float)
    return {'mae':float(mean_absolute_error(actual,predicted)),'rmse':float(np.sqrt(mean_squared_error(actual,predicted))),'n':len(rows)}
def calculate(records,parameters):
    p=validate_parameters(parameters)
    if len(records)<40:raise ValueError('至少40条独立工艺样本，才能拆分训练与留出验证')
    X=np.array([[r['values'][f] for f in FEATURES] for r in records],float);y=np.array([r['values']['minutes'] for r in records],float)
    indices=np.arange(len(records));train,test=train_test_split(indices,test_size=.25,random_state=42)
    cv=KFold(3,shuffle=True,random_state=42);candidates=[]
    for subset in SUBSETS:
        cols=[FEATURES.index(f) for f in subset]
        model=RandomForestRegressor(n_estimators=p['trees'],max_depth=p['depth'],random_state=42,n_jobs=1)
        errors=-cross_val_score(model,X[train][:,cols],y[train],cv=cv,scoring='neg_mean_absolute_error',n_jobs=1)
        candidates.append({'features':subset,'cv_mae':float(errors.mean()),'fold_mae':errors.tolist()})
    best=min(candidates,key=lambda c:c['cv_mae']);cols=[FEATURES.index(f) for f in best['features']]
    model=RandomForestRegressor(n_estimators=p['trees'],max_depth=p['depth'],random_state=42,n_jobs=1).fit(X[train][:,cols],y[train]);pred=model.predict(X[test][:,cols])
    importance=permutation_importance(model,X[test][:,cols],y[test],n_repeats=5,random_state=42,scoring='neg_mean_absolute_error',n_jobs=1)
    features=[{'feature':f,'mae_increase':float(v),'std':float(s)} for f,v,s in zip(best['features'],importance.importances_mean,importance.importances_std)]
    rows=[{'reference':records[i]['reference'],'actual_minutes':float(y[i]),'predicted_minutes':float(pred[j]),'error_minutes':float(pred[j]-y[i])} for j,i in enumerate(test)]
    return {'summary':[{'label':'留出 MAE / min','value':float(mean_absolute_error(y[test],pred))},{'label':'留出 RMSE / min','value':float(np.sqrt(mean_squared_error(y[test],pred)))},{'label':'训练样本','value':len(train)},{'label':'留出样本','value':len(test)}], 'candidates':candidates,'selected_features':best['features'],'importance':features,'train_indices':train.tolist(),'holdout_indices':test.tolist(),'training_checksum':hashlib.sha256(X[train].tobytes()+y[train].tobytes()).hexdigest(),'rows':rows,'columns':['reference','actual_minutes','predicted_minutes','error_minutes'],'chart':[{'label':x['feature'],'value':x['mae_increase']} for x in features],'explanation':'固定seed先拆25%留出；仅训练集3折MAE选择特征子集；最终森林在全部训练行拟合。图为留出置换MAE增量，可为负值，不是因果效应或概率区间。','checksum':hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest(),'seed':42}
