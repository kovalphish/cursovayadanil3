# ТехноМаркет — интернет-магазин бытовой техники
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os, uuid
from functools import wraps
from werkzeug.utils import secure_filename
import logging

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key_2026')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# БД
db_url = os.environ.get('DATABASE_URL', 'sqlite:////tmp/app.db')
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', '/tmp/uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

# --- Модели ---
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(100))
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0)
    description = db.Column(db.Text)
    image = db.Column(db.String(200), default='default.png')

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(200), nullable=False)
    customer_phone = db.Column(db.String(50))
    customer_address = db.Column(db.Text)
    total = db.Column(db.Float, default=0)
    status = db.Column(db.String(50), default='new')
    date = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('OrderItem', backref='order', cascade='all, delete-orphan')

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    product_name = db.Column(db.String(200))
    price = db.Column(db.Float)
    quantity = db.Column(db.Integer, default=1)

# --- Утилиты ---
@app.teardown_appcontext
def close_db(exc):
    if exc: db.session.rollback()
    db.session.remove()

def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get('admin'): return redirect(url_for('admin_login'))
        return f(*a, **kw)
    return wrap

def allowed_file(fn):
    return '.' in fn and fn.rsplit('.',1)[1].lower() in {'png','jpg','jpeg','gif','webp'}

def save_image(img_file):
    fn = secure_filename(img_file.filename)
    ext = fn.rsplit('.',1)[1].lower()
    name = f"{uuid.uuid4().hex}.{ext}"
    img_file.save(os.path.join(app.config['UPLOAD_FOLDER'], name))
    return name

# --- API: товары (для корзины на JS) ---
@app.route('/api/products')
def api_products():
    products = Product.query.all()
    return jsonify([{'id':p.id,'name':p.name,'price':p.price,'image':p.image,'stock':p.stock,'category':p.category or ''} for p in products])

# --- Страницы ---
@app.route('/')
def index():
    try:
        products = Product.query.limit(12).all()
    except:
        products = []
    return render_template('index.html', products=products)

@app.route('/products')
def products_page():
    search = request.args.get('search','')
    cat = request.args.get('category','')
    q = Product.query
    if search: q = q.filter(Product.name.ilike(f'%{search}%'))
    if cat: q = q.filter_by(category=cat)
    try:
        prods = q.all()
        categories = [c[0] for c in db.session.query(Product.category).distinct() if c[0]]
    except:
        prods, categories = [], []
    return render_template('products.html', products=prods, categories=categories, search=search, current_category=cat)

@app.route('/product/<int:id>')
def product_detail(id):
    p = Product.query.get_or_404(id)
    return render_template('product_detail.html', product=p)

# --- Корзина (JS + localStorage) ---
@app.route('/cart')
def cart_page():
    return render_template('cart.html')

@app.route('/checkout', methods=['POST'])
def checkout():
    data = request.get_json(force=True)
    if not data or not data.get('items'):
        return jsonify({'error':'Корзина пуста'}), 400
    name = data.get('name','').strip()
    phone = data.get('phone','').strip()
    address = data.get('address','').strip()
    if not all([name, phone, address]):
        return jsonify({'error':'Заполните все поля'}), 400
    try:
        total = sum(i['price']*i['qty'] for i in data['items'])
        order = Order(customer_name=name, customer_phone=phone, customer_address=address, total=total)
        db.session.add(order)
        db.session.flush()
        for i in data['items']:
            db.session.add(OrderItem(order_id=order.id, product_id=i['id'], product_name=i['name'], price=i['price'], quantity=i['qty']))
            p = Product.query.get(i['id'])
            if p: p.stock = max(0, p.stock - i['qty'])
        db.session.commit()
        return jsonify({'ok':True, 'order_id':order.id})
    except Exception as e:
        db.session.rollback()
        logger.error(f'Checkout: {e}')
        return jsonify({'error':'Ошибка оформления'}), 500

# --- Админка ---
@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASS:
            session['admin'] = True
            return redirect(url_for('admin'))
        flash('Неверный пароль','error')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('index'))

@app.route('/admin')
@login_required
def admin():
    try:
        prods = Product.query.all()
        orders = Order.query.order_by(Order.date.desc()).all()
    except:
        prods, orders = [], []
    return render_template('admin.html', products=prods, orders=orders)

@app.route('/admin/product/add', methods=['GET','POST'])
@login_required
def product_add():
    if request.method == 'POST':
        try:
            img_name = 'default.png'
            f = request.files.get('image')
            if f and allowed_file(f.filename): img_name = save_image(f)
            p = Product(name=request.form['name'], category=request.form.get('category',''),
                       price=float(request.form['price']), stock=int(request.form.get('stock',0) or 0),
                       description=request.form.get('description',''), image=img_name)
            db.session.add(p); db.session.commit()
            flash('Товар добавлен','success')
        except Exception as e:
            db.session.rollback(); flash(f'Ошибка: {e}','error')
        return redirect(url_for('admin'))
    return render_template('product_form.html', title='Добавить товар', product=None)

@app.route('/admin/product/edit/<int:id>', methods=['GET','POST'])
@login_required
def product_edit(id):
    p = Product.query.get_or_404(id)
    if request.method == 'POST':
        try:
            p.name = request.form['name']
            p.category = request.form.get('category','')
            p.price = float(request.form['price'])
            p.stock = int(request.form.get('stock',0) or 0)
            p.description = request.form.get('description','')
            f = request.files.get('image')
            if f and allowed_file(f.filename): p.image = save_image(f)
            db.session.commit(); flash('Товар обновлен','success')
        except Exception as e:
            db.session.rollback(); flash(f'Ошибка: {e}','error')
        return redirect(url_for('admin'))
    return render_template('product_form.html', title='Редактировать товар', product=p)

@app.route('/admin/product/delete/<int:id>')
@login_required
def product_delete(id):
    try:
        db.session.delete(Product.query.get_or_404(id)); db.session.commit(); flash('Удалено','success')
    except: db.session.rollback(); flash('Ошибка','error')
    return redirect(url_for('admin'))

@app.route('/admin/order/status/<int:id>', methods=['POST'])
@login_required
def order_status(id):
    try:
        o = Order.query.get_or_404(id); o.status = request.form.get('status','new')
        db.session.commit(); flash('Статус обновлен','success')
    except: db.session.rollback(); flash('Ошибка','error')
    return redirect(url_for('admin'))

@app.route('/admin/order/delete/<int:id>')
@login_required
def order_delete(id):
    try:
        db.session.delete(Order.query.get_or_404(id)); db.session.commit(); flash('Удалено','success')
    except: db.session.rollback(); flash('Ошибка','error')
    return redirect(url_for('admin'))

# --- Статика ---
@app.route('/static/uploads/<fn>')
def uploaded_file(fn):
    # Пытаемся найти файл в папке загрузок
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], fn)
    if os.path.exists(upload_path):
        return send_from_directory(app.config['UPLOAD_FOLDER'], fn)
    
    # Если файл не найден, возвращаем заглушку
    return send_from_directory('static', 'default.png')

# Путь для доступа к загруженным изображениям из админки
@app.route('/uploads/<filename>')
def uploaded_file_alt(filename):
    return uploaded_file(filename)

@app.errorhandler(404)
def e404(e): return render_template('index.html', products=[]), 404

# --- Инициализация и добавление тестовых данных ---
with app.app_context():
    try:
        db.create_all()
        
        # Добавляем тестовые товары, если база пуста
        if Product.query.count() == 0:
            test_products = [
                Product(name="Холодильник Samsung RB-30J3000WW", category="Холодильники", price=49990, stock=10, 
                       description="Отличный холодильник с системой No Frost", image="default.png"),
                Product(name="Стиральная машина LG F2J3HS0W", category="Стиральные машины", price=38990, stock=8,
                       description="Стиральная машина с прямым приводом", image="default.png"),
                Product(name="Телевизор Sony KD-55X80J", category="Телевизоры", price=69990, stock=5,
                       description="4K HDR телевизор с Android TV", image="default.png"),
                Product(name="Микроволновая печь Panasonic NN-ST342M", category="Микроволновые печи", price=8990, stock=15,
                       description="Микроволновая печь с грилем", image="default.png"),
                Product(name="Пылесос Dyson V8 Absolute", category="Пылесосы", price=32990, stock=7,
                       description="Беспроводной пылесос высокой мощности", image="default.png"),
                Product(name="Электрочайник Bosch TWK3A011", category="Мелкая техника", price=2490, stock=20,
                       description="Электрический чайник из нержавеющей стали", image="default.png"),
                Product(name="Утюг Philips GC1905", category="Мелкая техника", price=1890, stock=25,
                       description="Паровой утюг с керамической подошвой", image="default.png"),
                Product(name="Посудомоечная машина Bosch SMS25AW01R", category="Посудомоечные машины", price=42990, stock=6,
                       description="Полностью встраиваемая посудомоечная машина", image="default.png"),
                Product(name="Мультиварка Redmond RMC-M90", category="Мелкая техника", price=5990, stock=12,
                       description="Мультиварка с 50 программами", image="default.png"),
                Product(name="Кондиционер Ballu BSD-09HN1", category="Климатическая техника", price=25990, stock=4,
                       description="Сплит-система с инвертором", image="default.png"),
                Product(name="Кофемашина De'Longhi ECAM22.110.B", category="Мелкая техника", price=45990, stock=3,
                       description="Автоматическая кофемашина", image="default.png"),
                Product(name="Фен Philips HP8232", category="Уход за волосами", price=2990, stock=18,
                       description="Фен с ионизацией и 3 режимами", image="default.png")
            ]
            for product in test_products:
                db.session.add(product)
            db.session.commit()
            logger.info("Добавлены тестовые товары")
            
    except Exception as e:
        logger.error(f'DB init: {e}')

application = app
if __name__ == '__main__': 
    app.run(debug=True, host='0.0.0.0', port=5000)
