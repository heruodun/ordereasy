import logging
import os
import sys
import threading
from logging.handlers import TimedRotatingFileHandler

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QMessageBox
from flask import Flask, request, jsonify, g
import time
import constants
import server_db
import server_feishu
import sqlite3
import server_schedule
from apscheduler.schedulers.background import BackgroundScheduler
from flask_cors import CORS
import server_search

import server_tsp_main

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
version = "1.0.0"

app.config['places'] = None
app.config['JSON_AS_ASCII'] = False  # 确保JSON支持中文


def init_logger():
    # 获取exe文件（或脚本）的当前路径
    if getattr(sys, 'frozen', False):
        # 如果程序是"冻结"状态（即被编译为exe），使用这种方式
        application_path = os.path.dirname(sys.executable)
    else:
        # 否则就是普通的Python脚本运行
        application_path = os.path.dirname(os.path.abspath(__file__))

    # 定义日志文件的路径（放在exe文件/脚本所在目录的logs子目录下）
    log_directory = os.path.join(application_path, 'logs')
    if not os.path.exists(log_directory):
        os.makedirs(log_directory)  # 如果logs目录不存在，则创建之

    log_file_path = os.path.join(log_directory, 'server_app.log')

    # 设置日志记录器
    handler = TimedRotatingFileHandler(log_file_path, when='midnight', backupCount=100)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(threadName)s %(levelname)s: %(message)s '
        '[in %(pathname)s:%(lineno)d]'
    ))

    if app.logger.hasHandlers():
        app.logger.handlers.clear()

    # 将日志处理器添加到 Flask 的默认日志记录器中
    app.logger.addHandler(handler)

    # 设置日志记录级别
    app.logger.setLevel(logging.INFO)


def get_addresses():
    return server_db.get_addresses()


def get_places():
    place_list = app.config['places']
    if place_list is None:
        addresses = server_db.get_addresses()
        place_list = [item['place'] for item in addresses]
        place_list.sort(key=constants.sort_key)
        app.config['places'] = place_list
    return place_list


# 记录所有请求的信息（可选）
@app.before_request
def before_request_logging():
    app.logger.info("Received request: %s %s", request.method, request.url)


#
#
# 记录所有响应的信息（可选）
@app.after_request
def after_request_logging(response):
    app.logger.info("Sending response: %s - %s", response.status, response.data)
    return response


def index():
    # 使用HTML的<style>来设置文本样式
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>''' + constants.COMPANY_NAME + '''打单服务''' + version + '''</title>
        <style>
            body, html {
                height: 100%;
                margin: 0;
                display: flex;
                justify-content: center;
                align-items: center;
            }
            .welcome {
                color: red;
                font-weight: bold;
                font-size:50px;
            }
        </style>
    </head>
    <body>
        <div class="welcome">欢迎来到''' + constants.COMPANY_NAME + '''打单服务!</div>
    </body>
    </html>
    '''


def call_remote_service_async(insert_data, msg_data, order_id):
    app.logger.info("call_remote_service_async begin to insert one order to feishu async %s", insert_data)
    app.logger.info("call_remote_service_async begin to send one msg to feishu async %s", msg_data)
    count = 0
    ok = False

    while not ok and count < 3:  # 使用while循环重试逻辑
        ok = server_feishu.write_one_record(insert_data)
        if ok:
            app.logger.info("call_remote_service_async insert one order to feishu async success %s", order_id)
            with app.app_context():
                db = server_db.get_db()
                server_db.update_order_status(db, constants.LOCAL_DATA_FIRST_INSERT, order_id)
                app.logger.info("call_remote_service_async update one order sync status to 1 success %s", order_id)
            server_feishu.send_one_message(msg_data)
        if not ok:
            # 如果请求没有成功，稍微等待一段时间再次重试
            time.sleep(1)  # 等待1秒再次尝试
    if not ok:
        # 这里处理所有尝试失败的逻辑，如记录日志或发送报警通知
        app.logger.error("Insert one order to feishu async %s times fail %s", count, insert_data)


def get_str_from_excel(excel_str):
    result_str = ""
    if excel_str is not None:
        result_str = str(excel_str).strip()
    return result_str


def order_post_data1(A12_H26):
    code = 0
    # 地址数据
    dizhi = A12_H26[0][1]

    # 去除前后空格和换行
    trimmed_string = dizhi.strip()
    # 去除中间的空格
    no_spaces_dizhi = trimmed_string.replace(" ", "")

    # 校验地址
    # places = get_places()
    # if no_spaces_dizhi not in places:
    #     code = constants.INSERT_ERROR_CODE_4
    #     return {}, code

    # 创单人员
    man = A12_H26[14][1]
    # 总条数
    total_count = A12_H26[12][1]
    # 长度数据
    # 条数数据

    str_l_d = ""
    # 规格数据
    guige = ""
    # 备注数据
    beizhu = ""
    sum_count = 0  # 初始化条数求和变量

    for i in range(0, 9):
        length = get_str_from_excel(A12_H26[i + 3][2])
        count = get_str_from_excel(A12_H26[i + 3][4])
        guige = guige + get_str_from_excel(A12_H26[i + 3][0])
        beizhu = beizhu + get_str_from_excel(A12_H26[i + 3][5])

        # 如果两个都为空，则跳过当前循环
        if length == "" and count == "":
            continue

        count = int(count) if count else 0
        sum_count += count  # 累加count

        # 如果length为空，则将length置为0
        if length == "":
            code = constants.INSERT_ERROR_CODE_5
            return {}, code

        # 如果length是整数，则去掉小数部分
        if isinstance(length, float) and length.is_integer():
            length_str = str(int(length))
        else:
            length_str = str(length)

        count_str = str(count)
        str_l_d += length_str + " x " + count_str + "，"
    str_l_d = str_l_d[:-1]

    # 如果total_count是整数字符串，则转换成整数
    total_count = int(total_count) if total_count else 0

    if sum_count != total_count:
        code = constants.INSERT_ERROR_CODE_1  # 如果累加的count总和与total_count不相等，则设置code为1
        return {}, code

    total_count_str = str(total_count)
    if man is None:
        man = ""
    if no_spaces_dizhi is None:
        no_spaces_dizhi = ""
    if guige is None:
        guige = ""
    if total_count_str is None:
        total_count_str = ""
    if beizhu is None:
        beizhu = ""

    new_record_data = {
        "printer": man,
        "address": no_spaces_dizhi,
        "content": "总条数：" + total_count_str + "\n\n规格：" + str(
            guige) + "\n\n长度和条数：" + str_l_d + "\n\n备注：" + str(beizhu)
    }
    return new_record_data, code


def order_post_data2(A12_H26):
    # 地址数据
    dizhi = A12_H26[0][1]
    # 创单人员
    man = A12_H26[14][1]
    # 规格 * 数量 单位
    str_g_l_d = "规格和数量："
    # 备注数据
    beizhu = get_str_from_excel(A12_H26[9][1])

    for i in range(0, 4):
        guige = get_str_from_excel(A12_H26[i + 3][1])
        count = get_str_from_excel(A12_H26[i + 3][4])
        danwei = get_str_from_excel(A12_H26[i + 3][6])

        # 如果两个都为空，则跳过当前循环
        if guige == "" and count == "":
            continue

        # 如果count为空，则将count置为0
        if count == "":
            count = 0
        str_g_l_d += guige + " X " + str(count) + " " + danwei + "，"
    str_g_l_d = str_g_l_d[:-1]

    if man is None:
        man = ""
    if dizhi is None:
        dizhi = ""
    if beizhu is None:
        beizhu = ""

    new_record_data = {
        "printer": man,
        "address": dizhi,
        "content": str_g_l_d + "\n\n备注：" + str(beizhu)
    }
    return new_record_data


def save_one_order(data):
    if not data:
        return jsonify({"error": "Missing data in JSON payload"}), 400
    db = server_db.get_db()
    try:
        current_timestamp = time.time()
        timestamp_ms = int(current_timestamp * 1000)
        data["print_time"] = timestamp_ms

        # 将时间戳转换为本地时间的 struct_time 对象
        local_time = time.localtime(current_timestamp)
        # 将 struct_time 对象格式化为字符串
        formatted_time = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
        data["order_trace"] = "打单人：" + data["printer"] + "，" + "打单时间：" + formatted_time
        record_id = server_db.insert_record(db, data)
        order_id = server_db.generate_and_update_order_id(db, record_id)
        db.commit()

        new_record_data = {
            "fields": {
                "订单编号": order_id,
                "编号": record_id,
                "地址": data["address"],
                "货物": data["content"],
                "打单时间": timestamp_ms,
                "打单人": data["printer"],
                "当前进度": constants.PRINT,
                "当前处理人": data["printer"],
                "当前处理时间": timestamp_ms,
                "总体进度": "打单人：" + data["printer"] + "，打单时间：" + formatted_time
            }
        }

        new_msg_data = {
            "order_id": order_id,
            "id": record_id,
            "address": data["address"],
            "content": data["content"],
            "cur_progress": constants.PRINT,
            "cur_man": data["printer"],
            "cur_time": formatted_time
        }
        thread = threading.Thread(target=call_remote_service_async, args=(new_record_data, new_msg_data, order_id,))
        thread.start()

        return jsonify({
            "status": "Order added and updated",
            "record_id": record_id,
            "order_id": order_id,
            "qr_code": str(order_id) + constants.QR_CODE_SUFFIXES,
            "create_time": formatted_time
        }), 201
    except sqlite3.Error as e:
        app.logger.error("Insert one order to local sync fail %s", data, e)
        return jsonify({"error": str(e)}), 500


def order1():
    if request.method == 'POST':
        a12_h26 = request.get_json()["data"]
        data, code = order_post_data1(a12_h26)
        if code == 0:
            return save_one_order(data)
        else:
            return jsonify({"error": constants.get_inset_err_msg(code)}), 400
    return jsonify({"error": "no method"}), 400


def order2():
    if request.method == 'POST':
        a12_h26 = request.get_json()["data"]
        data = order_post_data2(a12_h26)
        return save_one_order(data)
    return jsonify({"error": "no method"}), 400


def get_order_by_id():
    order_id = request.args.get('order_id')
    if not order_id:
        return jsonify({"error": "Missing 'order_id' in query parameters"}), 400
    order = server_db.get_order_by_id(order_id)
    if order:
        return jsonify(dict(order)), 200
    else:
        return jsonify({"error": "Order not found"}), 404


def local_orders():
    # 获取页码参数，默认为1
    page_no = request.args.get('pageno', 1, type=int)
    # 获取每页显示的记录数参数，默认为10
    per_page = request.args.get('perpage', 1000, type=int)

    # 计算开始的记录位置
    start = (page_no - 1) * per_page
    result = server_db.get_orders(per_page, start)
    # 响应请求
    return jsonify(result), 200


def local_addresses():
    result = get_addresses
    # 响应请求
    return jsonify(result), 200


def local_address_fsearch():
    keyword = request.args.get('key', '')  # 默认为空字符串
    app.logger.info(get_places())
    result = server_search.search(keyword, get_places())
    app.logger.info(f"key is {keyword}, value is {result}")
    return {"result": list(result)}


# 路径规划
def run_tsp():
    addressid = request.args.get('addressid', '')  # 默认为空字符串
    if addressid:
        ids = addressid.split(',')
        # (index, 经度, 纬度, address_id, 地址名称)
        nodes_data = server_db.query_addresses_by_ids(ids)
        logging.info(str(nodes_data))
        route = server_tsp_main.run(len(nodes_data), nodes_data, init_tsp())
        return route, 200

    else:
        return {"error": "No addressid provided"}, 400


def init_tsp():
    # 获取exe文件（或脚本）的当前路径
    if getattr(sys, 'frozen', False):
        # 如果程序是"冻结"状态（即被编译为exe），使用这种方式
        application_path = os.path.dirname(sys.executable)
    else:
        # 否则就是普通的Python脚本运行
        application_path = os.path.dirname(os.path.abspath(__file__))

    # tsp diagrams
    diagrams_directory = os.path.join(application_path, 'diagrams')
    if not os.path.exists(diagrams_directory):
        os.makedirs(diagrams_directory)
    return diagrams_directory


def init_db():
    server_db.init_db()
    return "Database initialized."


def login():
    phone = request.args.get('phone')
    password = request.args.get('password')
    if not phone:
        return jsonify({"error": "Missing 'phone' in query parameters"}), 400
    if not password:
        return jsonify({"error": "Missing 'password' in query parameters"}), 400

    code, name = server_feishu.read_users(phone, password)

    if code == 0:
        if name is not None:
            return name, 200
        else:
            return jsonify({"error": "user not found"}), 404
    else:
        return jsonify({"error": "user not found"}), 500


def sync_data1():
    scheduled_job1_30_d_local()
    return jsonify({"scheduled_job1_30_d_local run": "ok"}), 200


def sync_data2():
    scheduled_job2_14_d_remote_job()
    return jsonify({"scheduled_job2_14_d_remote_job run": "ok"}), 200


def sync_data3():
    scheduled_job3_update_local_addresses_job()
    return jsonify({"scheduled_job3_2_min_update_local_addresses_job run": "ok"}), 200


@app.errorhandler(Exception)
def handle_exception(e):
    # 对错误进行记录
    app.logger.error('%s', (e))
    # 通知报警系统
    send_notification('An error occurred: {}'.format(str(e)))
    return 'An internal error occurred.', 500


def send_notification(err_message):
    server_feishu.send_one_alert_message('Application Error Alert' + err_message)


@app.route('/health')
def health_check():
    return 'OK', 200


# 注册路由
app.add_url_rule('/', 'index', index)
# 创建模板一的一个订单
app.add_url_rule('/order1', 'order1', order1, methods=['POST'])

# 创建模板二的一个订单
app.add_url_rule('/order2', 'order2', order2, methods=['POST'])
# 获取一个订单
app.add_url_rule('/order', 'get_order_by_id', get_order_by_id, methods=['GET'])
# 初始化数据库
app.add_url_rule('/initdb', 'init_db', init_db, methods=['GET'])
# 用户登录
app.add_url_rule('/login', 'login', login, methods=['GET'])

app.add_url_rule('/sync1', 'sync1', sync_data1, methods=['GET'])

app.add_url_rule('/sync2', 'sync2', sync_data2, methods=['GET'])

app.add_url_rule('/sync3', 'sync3', sync_data3, methods=['GET'])

app.add_url_rule('/local/orders', 'local_orders', local_orders, methods=['GET'])

# 获取地址信息列表 多了经纬度
app.add_url_rule('/local/addresses', 'local_addresses', local_addresses, methods=['GET'])
# 模糊查询地址 hz->hz11、hz、杭州大厦、浙江杭州
app.add_url_rule('/local/address/fsearch', 'local_address_fsearch', local_address_fsearch, methods=['GET'])

# 获取地名列表
app.add_url_rule('/local/places', 'get_places', get_places, methods=['GET'])

app.add_url_rule('/remote/orders', 'remote_orders', local_orders, methods=['GET'])

app.add_url_rule('/run/tsp', 'run_tsp', run_tsp, methods=['GET'])

# 注册应用上下文的清理函数
app.teardown_appcontext(server_db.close_connection)


# 30天内本地创建的单子且未被同步到feishu
def scheduled_job1_30_d_local():
    with app.app_context():
        db = server_db.get_db()
        server_schedule.execute_job_without_transaction(db, constants.JOB_ONE)


def scheduled_job2_14_d_remote_job():
    with app.app_context():
        db = server_db.get_db()
        server_schedule.execute_job_without_transaction(db, constants.JOB_TWO)


# 2分钟一次更新远程地址到本地
def scheduled_job3_update_local_addresses_job():
    with app.app_context():
        db = server_db.get_db()
        server_schedule.scheduled_job3_update_local_addresses_job(db)


def init_job():
    # 初始化定时任务
    scheduler = BackgroundScheduler()
    # 每天凌晨2点执行
    scheduler.add_job(scheduled_job2_14_d_remote_job, 'cron', hour=2, minute=0)
    # 每隔5分钟一次
    scheduler.add_job(scheduled_job1_30_d_local, 'interval', minutes=2)
    # 每隔3分钟一次
    scheduler.add_job(scheduled_job3_update_local_addresses_job, 'interval', minutes=3)
    scheduler.start()


def start_flask_app():
    # 在应用启动时立即初始化数据库（通过访问/initdb路由）
    init_job()
    init_logger()
    app.run(host='0.0.0.0', port=5000, threaded=True, use_reloader=False)


def main():
    server_env = os.getenv(constants.SERVER_ENV_KEY)
    if server_env is None:
        # sys.exit("错误：环境变量 " + constants.SERVER_ENV_KEY + " 未配置，请设置后再运行程序。")
        app = QApplication(sys.argv)
        QMessageBox.critical(None, "环境变量错误",
                             "错误：环境变量 " + constants.SERVER_ENV_KEY + " 未配置，请设置后再运行程序。")
        sys.exit(1)

    app = QApplication([])
    # 获取当前文件夹的路径
    current_dir = os.path.dirname(os.path.realpath(__file__))
    # 构建图标的完整路径
    icon_path = os.path.join(current_dir, "icon.png")
    tray_icon = QSystemTrayIcon(QIcon(icon_path), app)
    tray_icon.setToolTip(constants.COMPANY_NAME + "打单服务")
    menu = QMenu()
    exit_action = menu.addAction("退出")
    tray_icon.setContextMenu(menu)
    exit_action.triggered.connect(app.quit)
    tray_icon.show()

    flask_thread = threading.Thread(target=start_flask_app)
    flask_thread.daemon = True
    flask_thread.start()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
