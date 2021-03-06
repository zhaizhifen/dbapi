#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time : 2019/9/16 9:32
# @Author : 马飞
# @File : dbapi.py
# @Func : dbops_api Server 提供数据库备份、同步API。
# @Software: PyCharm
import tornado.ioloop
import tornado.web
import tornado.options
import tornado.httpserver
import tornado.locale
from   tornado.options  import define, options
import datetime,json
import pymysql
import paramiko
import os,sys
from   crontab import CronTab

def get_time():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def print_dict(config):
    print('-'.ljust(85,'-'))
    print(' '.ljust(3,' ')+"name".ljust(20,' ')+'value')
    print('-'.ljust(85,'-'))
    for key in config:
        print(' '.ljust(3,' ')+key.ljust(20,' ')+'='+str(config[key]))
    print('-'.ljust(85,'-'))


def get_ds_mysql(ip,port,service ,user,password):
    conn = pymysql.connect(host=ip, port=int(port), user=user, passwd=password, db=service,
                           charset='utf8',cursorclass = pymysql.cursors.DictCursor)
    return conn

def get_db_mysql(config):
    return get_ds_mysql(config['db_ip'],config['db_port'],config['db_service'],config['db_user'],config['db_pass'])

def aes_decrypt(db,p_password,p_key):
    cr = db.cursor()
    sql="""select aes_decrypt(unhex('{0}'),'{1}') as password """.format(p_password,p_key[::-1])
    cr.execute(sql)
    rs=cr.fetchone()
    db.commit()
    cr.close()
    db.close()
    #print(rs['password'])
    print('aes_decrypt=',str(rs['password'],encoding = "utf-8"))
    #return rs['password']
    return str(rs['password'],encoding = "utf-8")

def write_log(msg):
    file_name   = '/tmp/dbapi_{0}.log'.format(options.port)
    file_handle = open(file_name, 'a+')
    file_handle.write(msg + '\n')
    file_handle.close()

def get_file_contents(filename):
    file_handle = open(filename, 'r')
    line = file_handle.readline()
    lines = ''
    while line:
        lines = lines + line
        line = file_handle.readline()
    lines = lines + line
    file_handle.close()
    return lines

def db_config():
    config={}
    config['db_ip']      = '10.2.39.18'
    config['db_port']    = '3306'
    config['db_user']    = 'puppet'
    config['db_pass']    = 'Puppet@123'
    config['db_service'] = 'puppet'
    config['db_mysql']   =  get_db_mysql(config)
    return config

def update_backup_status(p_tag):
    config = db_config()
    db     = config['db_mysql']
    cr     = db.cursor()
    result = get_db_config(p_tag)
    if result['code']!=200:
       return result
    v_cmd   = 'ps -ef |grep {0} | grep -v grep |wc -l'.format(p_tag)
    print(v_cmd)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    config = db_config()
    v_password = aes_decrypt(config['db_mysql'], result['msg']['server_pass'], result['msg']['server_user'])
    ssh.connect(hostname=result['msg']['server_ip']  , port=int(result['msg']['server_port']),
                username=result['msg']['server_user'], password=v_password)

    #execute command
    stdin, stdout,stderr=ssh.exec_command(v_cmd)
    #get result
    res, err = stdout.read(), stderr.read()
    ret = (res if res else err).decode().replace('\n','')
    ssh.close()
    #update table:t_db_config task_status column
    print(p_tag,'A'+ret+'B',ret==0,ret=='0',v_cmd)
    if ret=='0':
       cr.execute("update t_db_config set task_status=0 where db_tag='{0}'".format(p_tag))
       db.commit()
       cr.close()
       result['code'] = 0
       result['msg'] = '已停止!'
    else:
       cr.execute("update t_db_config set task_status=1 where db_tag='{0}'".format(p_tag))
       db.commit()
       cr.close()
       result['code'] = 1
       result['msg'] = '运行中!'
    return result


def get_task_tags():
    config = db_config()
    db     = config['db_mysql']
    cr     = db.cursor()
    cr.execute("SELECT  a.db_tag FROM t_db_config a  WHERE a.status='1'")
    rs = cr.fetchall()
    print(rs,type(rs))
    cr.close()
    return rs

def get_db_config(p_tag):
    config=db_config()
    db=config['db_mysql']
    cr=db.cursor()
    result = {}
    result['code'] = 200
    result['msg'] = ''

    #检测同步服务器是否有效
    if check_server_sync_status(p_tag) > 0:
        result['code'] = -1
        result['msg'] = '服务器已禁用!'
        return result

    #检测同步标识是否存在
    if check_db_config(p_tag) == 0:
        result['code'] = -1
        result['msg'] = '备份标识不存在!'
        return result

    #任务已禁用
    if check_backup_task_status(p_tag) > 0:
        result['code'] = -1
        result['msg'] = '备份任务已禁用!'
        return result

    cr.execute('''SELECT  a.db_tag,
                          c.ip   AS db_ip,
                          c.port AS db_port,
                          c.user AS db_user,
                          c.password AS db_pass,
                          a.expire,
                          a.bk_base,a.script_path,a.script_file,a.bk_cmd,a.run_time,
                          b.server_ip,b.server_port,b.server_user,b.server_pass,
                          a.comments,a.python3_home,a.backup_databases,a.api_server,a.status
                FROM t_db_config a,t_server b,t_db_source c
                WHERE a.server_id=b.id 
                  AND a.db_id=c.id
                  AND a.db_tag='{0}' 
                  AND b.status='1'
               '''.format(p_tag))
    rs=cr.fetchone()
    result['msg'] = rs
    cr.close()
    return result

def get_db_sync_config(p_tag):
    config=db_config()
    db=config['db_mysql']
    cr=db.cursor()
    result = {}
    result['code'] = 200
    result['msg'] = ''

    #检测同步服务器是否有效
    if check_server_sync_status(p_tag)>0:
       result['code'] = -1
       result['msg'] = '同步服务器已禁用!'
       return result

    #检测同步标识是否存在
    if check_db_sync_config(p_tag)==0:
       result['code'] = -1
       result['msg'] = '同步标识不存在!'
       return result

    #任务已禁用
    if check_sync_task_status(p_tag) > 0:
       result['code'] = -1
       result['msg'] = '同步任务已禁用!'
       return result

    cr.execute('''SELECT  a.sync_tag,a.sync_ywlx,
                          CASE WHEN c.service='' THEN 
                            CONCAT(c.ip,':',c.port,':',a.sync_schema,':',c.user,':',c.password)
                          ELSE
                            CONCAT(c.ip,':',c.port,':',c.service,':',c.user,':',c.password)
                          END AS sync_db_sour,                          
                          CASE WHEN d.service='' THEN 
                            CONCAT(d.ip,':',d.port,':',a.sync_schema,':',d.user,':',d.password)
                          ELSE
                            CONCAT(d.ip,':',d.port,':',d.service,':',d.user,':',d.password)
                          END AS sync_db_dest,                          
                          a.server_id,a.run_time,a.api_server,
                          LOWER(a.sync_table) AS sync_table,a.batch_size,a.batch_size_incr,a.sync_gap,a.sync_col_name,
                          a.sync_col_val,a.sync_time_type,a.script_path,a.script_file,a.comments,a.python3_home,
                          a.status,b.server_ip,b.server_port,b.server_user,b.server_pass
                FROM t_db_sync_config a,t_server b,t_db_source c,t_db_source d
                WHERE a.server_id=b.id 
                  AND a.sour_db_id=c.id
                  AND a.desc_db_id=d.id
                  AND a.sync_tag ='{0}' 
                  ORDER BY a.id,a.sync_ywlx
               '''.format(p_tag))
    rs=cr.fetchone()
    cr.close()
    result['msg']=rs
    return result

def check_db_config(p_tag):
    config=db_config()
    db=config['db_mysql']
    cr=db.cursor()
    cr.execute('''select count(0) from t_db_config where db_tag='{0}'
               '''.format(p_tag))
    rs=cr.fetchone()
    cr.close()
    return  rs['count(0)']

def check_db_sync_config(p_tag):
    config=db_config()
    db=config['db_mysql']
    cr=db.cursor()
    cr.execute('''select count(0) from t_db_sync_config where sync_tag='{0}'
               '''.format(p_tag))
    rs=cr.fetchone()
    cr.close()
    return  rs['count(0)']

def check_server_sync_status(p_tag):
    config=db_config()
    db=config['db_mysql']
    cr=db.cursor()
    cr.execute('''select count(0) from t_db_sync_config a,t_server b 
                  where a.server_id=b.id and a.sync_tag='{0}' and b.status='0'
               '''.format(p_tag))
    rs=cr.fetchone()
    cr.close()
    return  rs['count(0)']

def check_sync_task_status(p_tag):
    config=db_config()
    db=config['db_mysql']
    cr=db.cursor()
    cr.execute('''select count(0) from t_db_sync_config a,t_server b 
                  where a.server_id=b.id and a.sync_tag='{0}' and a.status='0'
               '''.format(p_tag))
    rs=cr.fetchone()
    cr.close()
    return  rs['count(0)']

def check_backup_task_status(p_tag):
    config=db_config()
    db=config['db_mysql']
    cr=db.cursor()
    cr.execute('''select count(0) from t_db_config a,t_server b 
                  where a.server_id=b.id and a.db_tag='{0}' and a.status='0'
               '''.format(p_tag))
    rs=cr.fetchone()
    cr.close()
    return  rs['count(0)']


def check_tab_exists(p_tab,p_where):
    config=db_config()
    db=config['db_mysql']
    cr=db.cursor()
    cr.execute('''select count(0) from {0} {1}
               '''.format(p_tab,p_where))
    rs=cr.fetchone()
    cr.close()
    return  rs['count(0)']

def save_sync_log(config):
    result = {}
    result['code'] = 200
    result['msg'] = 'success'
    db=db_config()['db_mysql']
    cr=db.cursor()
    v_sql='''insert into t_db_sync_tasks_log(sync_tag,create_date,duration,amount) values('{0}','{1}','{2}','{3}')
          '''.format(config['sync_tag'],config['create_date'],config['duration'],config['amount'])

    write_log(get_time())
    write_log(v_sql)
    cr.execute(v_sql)
    db.commit()
    cr.close()
    return result

def save_sync_log_detail(config):
    result = {}
    result['code'] = 200
    result['msg'] = 'success'
    db=db_config()['db_mysql']
    cr=db.cursor()
    v_sql='''insert into t_db_sync_tasks_log_detail(sync_tag,create_date,sync_table,sync_amount,duration) 
              values('{0}','{1}','{2}','{3}','{4}')
          '''.format(config['sync_tag'],config['create_date'],config['sync_table'],config['sync_amount'],config['duration'])

    write_log(get_time())
    write_log(v_sql)
    cr.execute(v_sql)
    db.commit()
    cr.close()
    return result

def save_backup_total(config):
    result = {}
    result['code'] = 200
    result['msg'] = 'success'
    db=db_config()['db_mysql']
    cr=db.cursor()
    v_where = " where db_tag='{0}' and create_date='{1}'". \
               format(config['db_tag'], config['create_date'])
    if check_tab_exists('t_db_backup_total',v_where)==0:
        v_sql='''insert into t_db_backup_total(db_tag,create_date,bk_base,total_size,start_time,end_time,elaspsed_backup,elaspsed_gzip,status)
                  values('{0}','{1}','{2}','{3}','{4}','{5}','{6}','{7}','{8}')
              '''.format(config['db_tag'],config['create_date'],config['bk_base'],config['total_size'],
     config['start_time'],config['end_time'],config['elaspsed_backup'],
     config['elaspsed_gzip'],config['status'])

    else:
        v_sql='''update t_db_backup_total
                    set create_date = '{0}',
                        bk_base     = '{1}',
                        total_size  = '{2}',
                        start_time  = '{3}',
                        end_time    = '{4}',
                        elaspsed_backup = '{5}',
                        elaspsed_gzip = '{6}',
                        status = '{7}'
                  where db_tag = '{8}'
              '''.format(config['create_date'], config['bk_base'], config['total_size'],config['start_time'],
    config['end_time'], config['elaspsed_backup'],config['elaspsed_gzip'],
    config['status'],config['db_tag'])
    write_log(get_time())
    write_log(v_sql)
    cr.execute(v_sql)
    db.commit()
    cr.close()
    return result

def save_backup_detail(config):
    result = {}
    result['code'] = 200
    result['msg'] = 'success'
    db=db_config()['db_mysql']
    cr=db.cursor()
    v_where=" where db_tag='{0}' and db_name='{1}' and create_date='{2}'".\
               format(config['db_tag'] ,config['db_name'],config['create_date'])
    if check_tab_exists('t_db_backup_detail',v_where)==0:
        v_sql='''insert into t_db_backup_detail(
                      db_tag,create_date,db_name,bk_path,file_name,db_size,
                      start_time,end_time,elaspsed_backup,elaspsed_gzip,status,error)
                   values('{0}','{1}','{2}','{3}','{4}','{5}','{6}','{7}','{8}','{9}','{10}','{11}')
              '''.format(config['db_tag'],config['create_date'],config['db_name'],config['bk_path'],
                         config['file_name'],config['db_size'],config['start_time'],config['end_time'],
                         config['elaspsed_backup'],config['elaspsed_gzip'],config['status'],config['error'])

    else:
        v_sql='''update t_db_backup_detail
                    set bk_path     = '{0}',
                        file_name   = '{1}',
                        db_size     = '{2}',
                        start_time  = '{3}',
                        end_time    = '{4}',
                        elaspsed_backup = '{5}',
                        elaspsed_gzip   = '{6}',
                        status = '{7}',
                        error  = '{8}'
                    where db_tag = '{9}' and db_name='{10}' and create_date='{11}'
                    '''.format(config['bk_path'],config['file_name'],config['db_size'],
                    config['start_time'],config['end_time'], config['elaspsed_backup'],config['elaspsed_gzip'],
                    config['status'],config['error'],config['db_tag'],config['db_name'],config['create_date'])
    write_log(get_time())
    write_log(v_sql)
    cr.execute(v_sql)
    db.commit()
    cr.close()
    return result

def  write_remote_crontab(v_tag):
    result = get_db_config(v_tag)
    if result['code']!=200:
       return result
    v_cmd   = '{0}/db_backup.sh {1} {2}'.format(result['msg']['script_path'],result['msg']['script_file'],v_tag)
    v_cron0 = '''echo -e "#{0}" >/tmp/conf'''.format(v_tag)
    v_cron1 = '''
                 crontab -l >> /tmp/conf && sed -i "/{0}/d" /tmp/conf && echo -e "\n#{1} tag={2}\n{3} {4} &>/dev/null &" >> /tmp/conf && crontab /tmp/conf       
              '''.format(v_tag,result['msg']['comments'],v_tag,result['msg']['run_time'],v_cmd)

    v_cron1_= '''
                 crontab -l > /tmp/conf && sed -i "/{0}/d" /tmp/conf && echo  -e "\n#{1} tag={2}\n#{3} {4} &>/dev/null &" >> /tmp/conf
              '''.format(v_tag, result['msg']['comments'], v_tag, result['msg']['run_time'], v_cmd)


    v_cron2 = '''sed -i '/^$/{N;/\\n$/D};' /tmp/conf'''
    v_cron3 = '''crontab /tmp/conf'''

    print(v_cron0)
    print(v_cron1)
    print(v_cron2)
    print(v_cron3)

    ssh = paramiko.SSHClient()
    print('Remote crontab update ....1')
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print('Remote crontab update ....2')
    config = db_config()
    v_password = aes_decrypt(config['db_mysql'], result['msg']['server_pass'], result['msg']['server_user'])
    ssh.connect(hostname=result['msg']['server_ip']  , port=int(result['msg']['server_port']),
                username=result['msg']['server_user'], password=v_password)
    print('Remote crontab update ....')
    ssh.exec_command(v_cron0)

    if result['msg']['status'] == '1':
        ssh.exec_command(v_cron1)
    else:
        ssh.exec_command(v_cron1_)

    ssh.exec_command(v_cron2)
    ssh.exec_command(v_cron3)
    print('Remote crontab update complete!')
    ssh.close()
    return result

def run_remote_backup_task(v_tag):
    result = get_db_config(v_tag)
    if result['code']!=200:
       return result
    v_cmd   = 'nohup {0}/db_backup.sh {1} {2} &>/tmp/backup.log &'.\
               format(result['msg']['script_path'],result['msg']['script_file'],v_tag)
    print(v_cmd)
    ssh = paramiko.SSHClient()
    print('Remote crontab update ....1')
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print('Remote crontab update ....2')
    config = db_config()
    v_password = aes_decrypt(config['db_mysql'], result['msg']['server_pass'], result['msg']['server_user'])
    ssh.connect(hostname=result['msg']['server_ip']  , port=int(result['msg']['server_port']),
                username=result['msg']['server_user'], password=v_password)
    ssh.exec_command(v_cmd)
    print('Remote backup_task is running !')
    ssh.close()
    return result

def run_remote_sync_task(v_tag):
    result = get_db_sync_config(v_tag)
    if result['code']!=200:
       return result

    v_cmd   = 'nohup {0}/db_sync.sh {1} {2} &'.format(result['msg']['script_path'], result['msg']['script_file'], v_tag)
    print(v_cmd)
    ssh = paramiko.SSHClient()
    print('Remote crontab update ....1')
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print('Remote crontab update ....2')
    config = db_config()
    v_password = aes_decrypt(config['db_mysql'], result['msg']['server_pass'], result['msg']['server_user'])
    ssh.connect(hostname=result['msg']['server_ip']  , port=int(result['msg']['server_port']),
                username=result['msg']['server_user'], password=v_password)
    ssh.exec_command(v_cmd)
    print('Remote backup_task is running !')
    ssh.close()
    return result

def stop_remote_backup_task(v_tag):
    result = get_db_config(v_tag)
    if result['code']!=200:
       return result

    v_cmd = """ps -ef | grep {0} |grep -v grep | awk '{print $2}'  | xargs kill -9""".format(v_tag)
    print(v_cmd)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    config = db_config()
    v_password = aes_decrypt(config['db_mysql'], result['msg']['server_pass'], result['msg']['server_user'])
    ssh.connect(hostname=result['msg']['server_ip']  , port=int(result['msg']['server_port']),
                username=result['msg']['server_user'], password=v_password)
    ssh.exec_command(v_cmd)
    print('Remote backup task:{0} is stopping !'.format(v_tag))
    ssh.close()
    return result

def stop_remote_sync_task(v_tag):
    result = get_db_sync_config(v_tag)
    if result['code']!=200:
       return result
    v_cmd1 = """ps -ef | grep {0} |grep -v grep | awk '{print $2}'  | wc -l""".format(v_tag)
    v_cmd2 = """ps -ef | grep {0} |grep -v grep | awk '{print $2}'  | xargs kill -9""".format(v_tag)
    print(v_cmd1)
    print(v_cmd2)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    config = db_config()
    v_password = aes_decrypt(config['db_mysql'], result['msg']['server_pass'], result['msg']['server_user'])
    ssh.connect(hostname=result['msg']['server_ip']  , port=int(result['msg']['server_port']),
                username=result['msg']['server_user'], password=v_password)

    stdin, stdout, stderr = ssh.exec_command(v_cmd1)
    result = stdout.read()
    result = str(result, encoding='utf-8')
    print('stop_remote_sync_task->stdout=',result)
    if result=='0':
       result['code'] = -1
       result['msg'] = '该任务未运行!'
       ssh.close()
       return result
    else:
       ssh.exec_command(v_cmd2)
       result['code'] = 200
       result['msg'] = '任务:{0}已停止!'.format(v_tag)
       ssh.close()
       return result

def write_remote_crontab_sync(v_tag):
    result = get_db_sync_config(v_tag)
    if result['code']!=200:
       return result

    v_cmd = '{0}/db_sync.sh {1} {2}'.format(result['msg']['script_path'],result['msg']['script_file'], v_tag)

    v_cron = '''
               crontab -l > /tmp/conf && sed -i "/{0}/d" /tmp/conf && echo  -e "\n#{1} tag={2}\n{3} {4} &>/dev/null &" >> /tmp/conf
             '''.format(v_tag,result['msg']['comments'],v_tag,result['msg']['run_time'],v_cmd)

    v_cron_ = '''
                crontab -l > /tmp/conf && sed -i "/{0}/d" /tmp/conf && echo  -e "\n#{1} tag={2}\n#{3} {4} &>/dev/null &" >> /tmp/conf
             '''.format(v_tag, result['msg']['comments'], v_tag, result['msg']['run_time'], v_cmd)


    v_cron2 ='''sed -i '/^$/{N;/\\n$/D};' /tmp/conf'''
    v_cron3 ='''crontab /tmp/conf'''

    # Decryption password
    config = db_config()
    print('config[db_mysql=', config['db_mysql'])
    print(result['msg']['server_pass'], result['msg']['server_user'])
    v_password = aes_decrypt(config['db_mysql'], result['msg']['server_pass'], result['msg']['server_user'])
    print('write_remote_crontab_sync ->v_password=', v_password)

    #connect server
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=result['msg']['server_ip'], port=int(result['msg']['server_port']),
                username=result['msg']['server_user'], password=v_password)
    #exec v_cron
    if result['msg']['status']=='1':
       ssh.exec_command(v_cron)
    else:
       ssh.exec_command(v_cron_)

    ssh.exec_command(v_cron2)
    ssh.exec_command(v_cron3)
    print('Remote crontab update complete!')
    ssh.close()
    return result

def transfer_remote_file(v_tag):
    result  = get_db_config(v_tag)
    print('transfer_remote_file=',result)
    if result['code']!=200:
       return result

    config=db_config()
    print('config[db_mysql=',config['db_mysql'])
    print(result['msg']['server_pass'],result['msg']['server_user'])
    v_password=aes_decrypt(config['db_mysql'],result['msg']['server_pass'],result['msg']['server_user'])
    print('transfer_remote_file ->v_password=',v_password)
    transport = paramiko.Transport((result['msg']['server_ip'], int(result['msg']['server_port'])))
    transport.connect(username=result['msg']['server_user'], password=v_password)
    sftp = paramiko.SFTPClient.from_transport(transport)

    #replace script file
    templete_file = './templete/{0}'.format(result['msg']['script_file'])
    local_file    = './script/{0}'.format(result['msg']['script_file'])
    remote_file   = '{0}/{1}'.format(result['msg']['script_path'], result['msg']['script_file'])
    print('templete_file=', templete_file)
    print('local_file=', local_file)
    print('remote_file=', remote_file)
    os.system('cp -f {0} {1}'.format(templete_file, local_file))
    with open(local_file, 'w') as obj_file:
        obj_file.write(get_file_contents(templete_file).
                       replace('$$API_SERVER$$', result['msg']['api_server']))

    #send .py file
    local_file = './script/{0}'.format(result['msg']['script_file'])
    remote_file = '{0}/{1}'.format(result['msg']['script_path'],result['msg']['script_file'])
    sftp.put(localpath=local_file, remotepath=remote_file)
    print('Script:{0} send to {1} ok.'.format(local_file, remote_file))

    #send .sh file
    templete_file = './templete/db_backup.sh'
    local_file    = './script/db_backup.sh'
    remote_file   = '{0}/db_backup.sh'.format(result['msg']['script_path'])

    os.system('cp -f {0} {1}'.format(templete_file,local_file))
    print('templete_file=',templete_file)
    print('local_file=',local_file)
    print('remote_file=',remote_file)
    with open(local_file, 'w') as obj_file:
        obj_file.write(get_file_contents(templete_file).
                       replace('$$PYTHON3_HOME$$',result['msg']['python3_home']).
                       replace('$$SCRIPT_PATH$$',result['msg']['script_path']))
    sftp.put(localpath=local_file, remotepath=remote_file)
    print('Script:{0} send to {1} ok.'.format(local_file,remote_file))
    transport.close()
    return result

def transfer_remote_file_sync(v_tag):
    print('transfer_remote_file_sync!')
    result = {}
    result['code'] = 200
    result['msg']  = ''
    result = get_db_sync_config(v_tag)
    print('transfer_remote_file_sync=',result)
    if result['code']!=200:
       return result

    #Decryption password
    config = db_config()
    print('config[db_mysql=', config['db_mysql'])
    print(result['msg']['server_pass'], result['msg']['server_user'])
    v_password = aes_decrypt(config['db_mysql'], result['msg']['server_pass'], result['msg']['server_user'])
    print('transfer_remote_file_sync ->v_password=', v_password)

    transport = paramiko.Transport((result['msg']['server_ip'], int(result['msg']['server_port'])))
    transport.connect(username=result['msg']['server_user'], password=v_password)
    sftp = paramiko.SFTPClient.from_transport(transport)

    #replace script file
    templete_file = './templete/{0}'.format(result['msg']['script_file'])
    local_file    = './script/{0}'.format(result['msg']['script_file'])
    remote_file   = '{0}/{1}'.format(result['msg']['script_path'], result['msg']['script_file'])
    os.system('cp -f {0} {1}'.format(templete_file, local_file))
    with open(local_file, 'w') as obj_file:
        obj_file.write(get_file_contents(templete_file).
                       replace('$$API_SERVER$$', result['msg']['api_server']))

    #create sync directory
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=result['msg']['server_ip'], port=int(result['msg']['server_port']),
                username=result['msg']['server_user'], password=v_password)
    ssh.exec_command('mkdir -p {0}'.format(result['msg']['script_path']))
    print("remote sync directory '{0}' created!".format(result['msg']['script_path']))

    #send .py file
    local_file  = './script/{0}'.format(result['msg']['script_file'])
    remote_file = '{0}/{1}'.format(result['msg']['script_path'],result['msg']['script_file'])
    print('transfer_remote_file_sync'+'$'+local_file+'$'+remote_file)
    sftp.put(localpath=local_file, remotepath=remote_file)
    print('Script:{0} send to {1} ok.'.format(local_file, remote_file))

    #send mysql_sync.sh file
    templete_file = './templete/db_sync.sh'
    local_file    = './script/db_sync.sh'
    remote_file   = '{0}/db_sync.sh'.format(result['msg']['script_path'])
    os.system('cp -f {0} {1}'.format(templete_file, local_file))
    print('templete_file=',templete_file)
    print('local_file=',local_file)
    print('remote_file=',remote_file)
    with open(local_file, 'w') as obj_file:
        obj_file.write(get_file_contents(templete_file).
                       replace('$$PYTHON3_HOME$$', result['msg']['python3_home']).
                       replace('$$SCRIPT_PATH$$' , result['msg']['script_path']))
    sftp.put(localpath=local_file, remotepath=remote_file)
    write_log('Script:{0} send to {1} ok.'.format(local_file, remote_file))
    transport.close()
    ssh.close()
    return result

def run_remote_cmd(v_tag):
    result = get_db_config(v_tag)
    if result['code'] != 200:
        return result
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    config=db_config()
    v_password = aes_decrypt(config['db_mysql'], result['msg']['server_pass'], result['msg']['server_user'])
    ssh.connect(hostname=result['msg']['server_ip'], port=int(result['msg']['server_port']),
                username=result['msg']['server_user'],password=v_password)
    remote_file1 = '{0}/{1}'.format(result['msg']['script_path'], result['msg']['script_file'])
    remote_file2 = '{0}/{1}'.format(result['msg']['script_path'], 'db_backup.sh')
    remote_cmd1  = 'mkdir -p {0}'.format(result['msg']['script_path']+'/config')
    ssh.exec_command('chmod +x {0}'.format(remote_file1))
    ssh.exec_command('chmod +x {0}'.format(remote_file2))
    ssh.exec_command(remote_cmd1)
    ssh.close()
    return result

def run_remote_cmd_sync(v_tag):
    # Init dict
    result = {}
    result['code'] = 200
    result['msg'] = ''
    print('run_remote_cmd_sync!')
    result = get_db_sync_config(v_tag)
    if result['code'] != 200:
        return result

    # Decryption password
    config = db_config()
    print('config[db_mysql=', config['db_mysql'])
    print(result['msg']['server_pass'], result['msg']['server_user'])
    v_password = aes_decrypt(config['db_mysql'], result['msg']['server_pass'], result['msg']['server_user'])
    print('run_remote_cmd_sync ->v_password=', v_password)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=result['msg']['server_ip'] ,port=int(result['msg']['server_port']),
                username=result['msg']['server_user'],password=v_password)
    print('run_remote_cmd_sync! connect!')
    remote_file1 = '{0}/{1}'.format(result['msg']['script_path'], result['msg']['script_file'])
    remote_file2 = '{0}/{1}'.format(result['msg']['script_path'], 'db_sync.sh')
    remote_cmd1  = 'mkdir -p {0}'.format(result['msg']['script_path'] + '/config')
    ssh.exec_command('chmod +x {0}'.format(remote_file1))
    ssh.exec_command('chmod +x {0}'.format(remote_file2))
    ssh.exec_command(remote_cmd1)
    print('run_remote_cmd_sync! exec_command!')
    ssh.close()
    return result

class read_config_backup(tornado.web.RequestHandler):
    def post(self):
        try:
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            v_tag     = self.get_argument("tag")
            result    = get_db_config(v_tag)
            v_json    = json.dumps(result)
            print("{0} dbops api interface /read_config_backup success!".format(get_time()))
            print("入口参数：\n\t{0}".format(v_tag))
            print("出口参数：")
            print(result['msg'] )
            self.write(v_json)
        except Exception as e:
            print(str(e))

class read_db_decrypt(tornado.web.RequestHandler):
    def post(self):
        try:
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            v_pass     = self.get_argument("password")
            v_key      = self.get_argument("key")
            config     = db_config()
            db         = config['db_mysql']
            v_new_pass = aes_decrypt(db,v_pass,v_key)
            result = {}
            result['code'] = 200
            result['msg']  = v_new_pass
            v_json = json.dumps(result)
            print("{0} dbops api interface /read_db_decrypt success!".format(get_time()))
            print(result['msg'])
            self.write(v_json)
        except Exception as e:
            print(str(e))


class write_backup_status(tornado.web.RequestHandler):
    def post(self):
        try:
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            rs=get_task_tags()
            for i in range(len(rs)):
                print(rs[i]['db_tag'])
                result = update_backup_status(rs[i]['db_tag'])
                print(rs[i]['db_tag'],result)
            print("{0} dbops api interface /read_backup_status success!".format(get_time()))
            self.write('update_backup_status')
        except Exception as e:
            print(str(e))


class write_backup_total(tornado.web.RequestHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        v_tag   = self.get_argument("tag")
        config  = json.loads(v_tag)
        result  = save_backup_total(config)
        v_json  = json.dumps(result)
        write_log("{0} dbops api interface /write_backup_total success!".format(get_time()))
        write_log("入口参数:")
        print_dict(config)
        write_log("出口参数：")
        print_dict(result)
        self.write(v_json)

class write_backup_detail(tornado.web.RequestHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        v_tag   = self.get_argument("tag")
        config  = json.loads(v_tag)
        result  = save_backup_detail(config)
        v_json  = json.dumps(result)
        write_log("{0} dbops api interface /write_backup_detail success!".format(get_time()))
        write_log("入口参数:")
        print_dict(config)
        write_log("出口参数：")
        print_dict(result)
        self.write(v_json)

class read_config_sync(tornado.web.RequestHandler):
    def post(self):
        try:
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            v_tag   = self.get_argument("tag")
            result  = get_db_sync_config(v_tag)
            v_json  = json.dumps(result)
            write_log("{0} dbops api interface /read_config_sync success!".format(get_time()))
            write_log("入口参数：\n\t{0}".format(v_tag))
            write_log("出口参数：")
            if result['code']==200:
                print_dict(result['msg'])
            self.write(v_json)
        except Exception as e:
            write_log(str(e))

class set_crontab_local(tornado.web.RequestHandler):
    ##################################################################################
    #  test: curl -XPOST 10.2.39.76:8181/set_crontab -d 'tag=mysql_10_2_39_80_3306'  #
    #  question：crontab execute more ,task repeat ?                                 #
    ##################################################################################
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        v_tag          = self.get_argument("tag")
        v_msg          = get_db_config(v_tag)
        v_cron         = CronTab(user=True)
        v_cmd          = '$PYTHON3_HOME/bin/python3 {0}/{1} -tag {2}'.format(v_msg['script_path'],v_msg['script_file'],v_msg['db_tag'])
        job            = v_cron.new(command=v_cmd)
        job.setall(v_msg['run_time'])
        job.enable()
        v_cron.write()
        result         = {}
        result['code'] = 200
        result['msg']  = v_msg
        v_json = json.dumps(result)
        write_log("{0} dbops api interface /set_crontab success!".format(get_time()))
        write_log("入口参数：\n\t{0}".format(v_tag))
        write_log("出口参数：")
        write_log(result['msg'] )
        self.write(v_json)

class set_crontab_remote(tornado.web.RequestHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        v_tag    = self.get_argument("tag")
        result   = write_remote_crontab(v_tag)
        v_json   = json.dumps(result)
        write_log("{0} dbops api interface /push_script success!".format(get_time()))
        write_log("入口参数：\n\t{0}".format(v_tag))
        write_log("出口参数：")
        print_dict(result['msg'] )
        self.write(v_json)

class push_script_remote(tornado.web.RequestHandler):
    def post(self):
        try:
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            v_tag   = self.get_argument("tag")
            print('v_tag=',v_tag)
            result  = transfer_remote_file(v_tag)
            if result['code'] != 200:
                v_json = json.dumps(result)
                self.write(v_json)
            else:
                result  = run_remote_cmd(v_tag)
                result  = write_remote_crontab(v_tag)
                v_json  = json.dumps(result)
                print("{0} dbops api interface /push_script_remote success!".format(get_time()))
                print("入口参数：\n\t{0}".format(v_tag))
                print("出口参数：")
                print_dict(result['msg'] )
                self.write(v_json)
        except Exception as e:
            print('push_script_remote error!')
            print(str(e))


class run_script_remote(tornado.web.RequestHandler):
    def post(self):
        try:
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            v_tag   = self.get_argument("tag")
            print('v_tag=',v_tag)
            result  = transfer_remote_file(v_tag)
            if result['code'] != 200:
                v_json = json.dumps(result)
                self.write(v_json)
            else:
                result  = run_remote_cmd(v_tag)
                result  = run_remote_backup_task(v_tag)
                v_json  = json.dumps(result)
                print("{0} dbops api interface /run_script_remote success!".format(get_time()))
                print("入口参数：\n\t{0}".format(v_tag))
                print("出口参数：")
                print_dict(result['msg'] )
                self.write(v_json)
        except Exception as e:
            print('push_script_remote error!')
            print(str(e))

class stop_script_remote(tornado.web.RequestHandler):
    def post(self):
        try:
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            v_tag   = self.get_argument("tag")
            print('v_tag=',v_tag)
            result  = stop_remote_backup_task(v_tag)
            v_json  = json.dumps(result)
            print("{0} dbops api interface /stop_script_remote success!".format(get_time()))
            print("入口参数：\n\t{0}".format(v_tag))
            print("出口参数：")
            print_dict(result['msg'] )
            self.write(v_json)
        except Exception as e:
            print('stop_script_remote error!')
            print(str(e))

class run_script_remote_sync(tornado.web.RequestHandler):
    def post(self):
        try:
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            v_tag   = self.get_argument("tag")
            print('v_tag=',v_tag)
            result  = transfer_remote_file_sync(v_tag)
            if result['code'] != 200:
                v_json = json.dumps(result)
                self.write(v_json)
            else:
                result  = run_remote_cmd_sync(v_tag)
                result  = run_remote_sync_task(v_tag)
                v_json  = json.dumps(result)
                print("{0} dbops api interface /run_script_remote_sync success!".format(get_time()))
                print("入口参数：\n\t{0}".format(v_tag))
                print("出口参数：")
                print_dict(result['msg'] )
                self.write(v_json)
        except Exception as e:
            print('push_script_remote error!')
            print(str(e))


class stop_script_remote_sync(tornado.web.RequestHandler):
    def post(self):
        try:
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            v_tag   = self.get_argument("tag")
            print('v_tag=',v_tag)
            result  = stop_remote_sync_task(v_tag)
            v_json  = json.dumps(result)
            print("{0} dbops api interface /stop_script_remote_sync success!".format(get_time()))
            print("入口参数：\n\t{0}".format(v_tag))
            print("出口参数：")
            print_dict(result['msg'] )
            self.write(v_json)
        except Exception as e:
            print('stop_script_remote_sync error!'+str(e))


class push_script_remote_sync(tornado.web.RequestHandler):
    def post(self):
        try:
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            v_tag   = self.get_argument("tag")
            result  = transfer_remote_file_sync(v_tag)
            if result['code']!=200:
               v_json = json.dumps(result)
               print('v_json=',v_json)
               self.write(v_json)
               return

            result  = run_remote_cmd_sync(v_tag)
            if result['code']!=200:
               v_json = json.dumps(result)
               print(v_json)
               self.write(v_json)
               return

            result  = write_remote_crontab_sync(v_tag)
            if result['code']!=200:
               v_json = json.dumps(result)
               print(v_json)
               self.write(v_json)
               return

            v_json  = json.dumps(result)
            write_log("{0} dbops api interface /push_script success!".format(get_time()))
            write_log("入口参数：\n\t{0}".format(v_tag))
            write_log("出口参数：")
            print_dict(result['msg'] )
            print(v_json)
            self.write(v_json)
        except Exception as e:
            print(str(e))
            write_log(str(e))

class write_sync_log(tornado.web.RequestHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        v_tag = self.get_argument("tag")
        config = json.loads(v_tag)
        result = save_sync_log(config)
        v_json = json.dumps(result)
        write_log("{0} dbops api interface /write_sync_log success!".format(get_time()))
        write_log("入口参数:")
        print_dict(config)
        write_log("出口参数：")
        print_dict(result)
        self.write(v_json)

class write_sync_log_detail(tornado.web.RequestHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        v_tag = self.get_argument("tag")
        config = json.loads(v_tag)
        result = save_sync_log_detail(config)
        v_json = json.dumps(result)
        write_log("{0} dbops api interface /write_sync_log_detail success!".format(get_time()))
        write_log("入口参数:")
        print_dict(config)
        write_log("出口参数：")
        print_dict(result)
        self.write(v_json)

define("port", default=sys.argv[1], help="run on the given port", type=int)

class Application(tornado.web.Application):
    def __init__(self):
        handlers = [

            #备份API接口
            (r"/read_config_backup" , read_config_backup),
            (r"/read_db_decrypt"    , read_db_decrypt),
            (r"/update_backup_status",write_backup_status),
            (r"/write_backup_total" , write_backup_total),
            (r"/write_backup_detail", write_backup_detail),
            (r"/set_crontab_local"  , set_crontab_local),
            (r"/set_crontab_remote" , set_crontab_remote),
            (r"/push_script_remote" , push_script_remote),
            (r"/run_script_remote"  , run_script_remote),
            (r"/stop_script_remote" , stop_script_remote),

            #同步API接口
            (r"/read_config_sync"       , read_config_sync),
            (r"/push_script_remote_sync", push_script_remote_sync),
            (r"/write_sync_log"         , write_sync_log),
            (r"/write_sync_log_detail"  , write_sync_log_detail),
            (r"/run_script_remote_sync",  run_script_remote_sync),
            (r"/stop_script_remote_sync", stop_script_remote_sync),
        ]
        tornado.web.Application.__init__(self, handlers)

if __name__ == '__main__':
    tornado.options.parse_command_line()
    http_server = tornado.httpserver.HTTPServer(Application())
    #http_server.listen(options.port)
    http_server.listen(sys.argv[1])
    print('Dbops Api Server running {0} port ...'.format(sys.argv[1]))
    tornado.ioloop.IOLoop.instance().start()



