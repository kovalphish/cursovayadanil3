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
db_url = os.environ.get('DATABASE_URL', 'sqlite:///shop.db')  # Изменено на локальный файл
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Создаем папки для загрузок
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

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
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], name)
    img_file.save(filepath)
    logger.info(f"Изображение сохранено: {filepath}")
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
    except Exception as e:
        logger.error(f'Index error: {e}')
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
            if f and allowed_file(f.filename): 
                img_name = save_image(f)
            p = Product(name=request.form['name'], category=request.form.get('category',''),
                       price=float(request.form['price']), stock=int(request.form.get('stock',0) or 0),
                       description=request.form.get('description',''), image=img_name)
            db.session.add(p)
            db.session.commit()
            flash('Товар добавлен','success')
        except Exception as e:
            db.session.rollback()
            logger.error(f'Product add error: {e}')
            flash(f'Ошибка: {e}','error')
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
            if f and allowed_file(f.filename): 
                p.image = save_image(f)
            db.session.commit()
            flash('Товар обновлен','success')
        except Exception as e:
            db.session.rollback()
            logger.error(f'Product edit error: {e}')
            flash(f'Ошибка: {e}','error')
        return redirect(url_for('admin'))
    return render_template('product_form.html', title='Редактировать товар', product=p)

@app.route('/admin/product/delete/<int:id>')
@login_required
def product_delete(id):
    try:
        db.session.delete(Product.query.get_or_404(id))
        db.session.commit()
        flash('Удалено','success')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Product delete error: {e}')
        flash('Ошибка','error')
    return redirect(url_for('admin'))

@app.route('/admin/order/status/<int:id>', methods=['POST'])
@login_required
def order_status(id):
    try:
        o = Order.query.get_or_404(id)
        o.status = request.form.get('status','new')
        db.session.commit()
        flash('Статус обновлен','success')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Order status error: {e}')
        flash('Ошибка','error')
    return redirect(url_for('admin'))

@app.route('/admin/order/delete/<int:id>')
@login_required
def order_delete(id):
    try:
        db.session.delete(Order.query.get_or_404(id))
        db.session.commit()
        flash('Удалено','success')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Order delete error: {e}')
        flash('Ошибка','error')
    return redirect(url_for('admin'))

# --- Статика ---
@app.route('/static/uploads/<filename>')
def uploaded_file(filename):
    """Отдача загруженных изображений"""
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    except Exception as e:
        logger.error(f"File not found: {filename}, error: {e}")
        return send_from_directory('static', 'default.png')

# --- Инициализация и добавление тестовых данных ---
with app.app_context():
    try:
        # Создаем таблицы
        db.create_all()
        
        # Копируем default.png в папку uploads если её нет
        default_img_path = os.path.join(app.config['UPLOAD_FOLDER'], 'default.png')
        if not os.path.exists(default_img_path):
            # Создаем простой default.png если его нет
            from PIL import Image, ImageDraw
            try:
                img = Image.new('RGB', (200, 200), color='#0066ff')
                draw = ImageDraw.Draw(img)
                draw.text((70, 90), "Нет фото", fill='white')
                img.save(default_img_path)
                logger.info("Создан default.png")
            except:
                logger.warning("Не удалось создать default.png")
        
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
            ]
            for product in test_products:
                db.session.add(product)
            db.session.commit()
            logger.info(f"Добавлено {len(test_products)} тестовых товаров")
            
    except Exception as e:
        logger.error(f'DB init error: {e}')

application = app

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
