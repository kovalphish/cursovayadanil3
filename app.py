# ТехноМаркет — интернет-магазин бытовой техники
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os
import uuid
from functools import wraps
import logging
import base64

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key_2026')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Настройка БД для Vercel
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

if DATABASE_URL:
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
else:
    # Fallback на SQLite в памяти (данные будут теряться при каждом запросе!)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    logger.warning("Using in-memory SQLite - data will be lost!")

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
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
    image_data = db.Column(db.Text)  # Храним base64 изображения
    image_type = db.Column(db.String(50), default='png')

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
    db.session.remove()

def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get('admin'):
            return redirect(url_for('admin_login'))
        return f(*a, **kw)
    return wrap

def allowed_file(fn):
    return '.' in fn and fn.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def save_image_base64(img_file):
    """Сохраняет изображение как base64 в БД"""
    if not img_file or not img_file.filename:
        return None, None
    
    data = img_file.read()
    ext = img_file.filename.rsplit('.', 1)[1].lower()
    
    # Конвертируем в base64
    b64_data = base64.b64encode(data).decode('utf-8')
    return b64_data, ext

# --- API ---
@app.route('/api/products')
def api_products():
    products = Product.query.all()
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'price': p.price,
        'stock': p.stock,
        'category': p.category or ''
    } for p in products])

# --- Страницы ---
@app.route('/')
def index():
    products = Product.query.limit(12).all()
    return render_template('index.html', products=products)

@app.route('/products')
def products_page():
    search = request.args.get('search', '')
    cat = request.args.get('category', '')
    q = Product.query
    
    if search:
        q = q.filter(Product.name.ilike(f'%{search}%'))
    if cat:
        q = q.filter_by(category=cat)
    
    prods = q.all()
    categories = [c[0] for c in db.session.query(Product.category).distinct() if c[0]]
    return render_template('products.html', products=prods, categories=categories, 
                          search=search, current_category=cat)

@app.route('/product/<int:id>')
def product_detail(id):
    p = Product.query.get_or_404(id)
    return render_template('product_detail.html', product=p)

@app.route('/image/<int:product_id>')
def get_product_image(product_id):
    """Возвращает изображение товара из БД"""
    product = Product.query.get_or_404(product_id)
    if product.image_data:
        import base64
        image_data = base64.b64decode(product.image_data)
        from flask import Response
        return Response(image_data, mimetype=f'image/{product.image_type}')
    return send_default_image()

def send_default_image():
    """Отправляет изображение по умолчанию"""
    from flask import send_file
    return send_file('static/default.png', mimetype='image/png')

# --- Корзина ---
@app.route('/cart')
def cart_page():
    return render_template('cart.html')

@app.route('/checkout', methods=['POST'])
def checkout():
    data = request.get_json(force=True)
    if not data or not data.get('items'):
        return jsonify({'error': 'Корзина пуста'}), 400
    
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    address = data.get('address', '').strip()
    
    if not all([name, phone, address]):
        return jsonify({'error': 'Заполните все поля'}), 400
    
    try:
        total = sum(i['price'] * i['qty'] for i in data['items'])
        order = Order(customer_name=name, customer_phone=phone, 
                     customer_address=address, total=total)
        db.session.add(order)
        db.session.flush()
        
        for i in data['items']:
            db.session.add(OrderItem(order_id=order.id, product_id=i['id'], 
                                    product_name=i['name'], price=i['price'], 
                                    quantity=i['qty']))
            p = Product.query.get(i['id'])
            if p:
                p.stock = max(0, p.stock - i['qty'])
        
        db.session.commit()
        return jsonify({'ok': True, 'order_id': order.id})
    except Exception as e:
        db.session.rollback()
        logger.error(f'Checkout: {e}')
        return jsonify({'error': 'Ошибка оформления'}), 500

# --- Админка ---
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASS:
            session['admin'] = True
            return redirect(url_for('admin'))
        flash('Неверный пароль', 'error')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('index'))

@app.route('/admin')
@login_required
def admin():
    prods = Product.query.all()
    orders = Order.query.order_by(Order.date.desc()).all()
    return render_template('admin.html', products=prods, orders=orders)

@app.route('/admin/product/add', methods=['GET', 'POST'])
@login_required
def product_add():
    if request.method == 'POST':
        try:
            image_b64 = None
            image_type = 'png'
            
            f = request.files.get('image')
            if f and f.filename and allowed_file(f.filename):
                image_b64, image_type = save_image_base64(f)
            
            p = Product(
                name=request.form['name'],
                category=request.form.get('category', ''),
                price=float(request.form['price']),
                stock=int(request.form.get('stock', 0) or 0),
                description=request.form.get('description', ''),
                image_data=image_b64,
                image_type=image_type
            )
            db.session.add(p)
            db.session.commit()
            flash('Товар успешно добавлен', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка: {e}', 'error')
            logger.error(f"Add product error: {e}")
        return redirect(url_for('admin'))
    return render_template('product_form.html', title='Добавить товар', product=None)

@app.route('/admin/product/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def product_edit(id):
    p = Product.query.get_or_404(id)
    if request.method == 'POST':
        try:
            p.name = request.form['name']
            p.category = request.form.get('category', '')
            p.price = float(request.form['price'])
            p.stock = int(request.form.get('stock', 0) or 0)
            p.description = request.form.get('description', '')
            
            f = request.files.get('image')
            if f and f.filename and allowed_file(f.filename):
                image_b64, image_type = save_image_base64(f)
                p.image_data = image_b64
                p.image_type = image_type
            
            db.session.commit()
            flash('Товар успешно обновлен', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка: {e}', 'error')
            logger.error(f"Edit product error: {e}")
        return redirect(url_for('admin'))
    return render_template('product_form.html', title='Редактировать товар', product=p)

@app.route('/admin/product/delete/<int:id>')
@login_required
def product_delete(id):
    try:
        p = Product.query.get_or_404(id)
        db.session.delete(p)
        db.session.commit()
        flash('Товар удален', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка: {e}', 'error')
    return redirect(url_for('admin'))

@app.route('/admin/order/status/<int:id>', methods=['POST'])
@login_required
def order_status(id):
    try:
        o = Order.query.get_or_404(id)
        o.status = request.form.get('status', 'new')
        db.session.commit()
        flash('Статус обновлен', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка: {e}', 'error')
    return redirect(url_for('admin'))

@app.route('/admin/order/delete/<int:id>')
@login_required
def order_delete(id):
    try:
        db.session.delete(Order.query.get_or_404(id))
        db.session.commit()
        flash('Заказ удален', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка: {e}', 'error')
    return redirect(url_for('admin'))

@app.errorhandler(404)
def e404(e):
    products = Product.query.limit(8).all()
    return render_template('index.html', products=products), 404

@app.errorhandler(500)
def e500(e):
    logger.error(f"500 error: {e}")
    return render_template('index.html', products=[]), 500

# --- Инициализация БД ---
def init_db():
    with app.app_context():
        try:
            db.create_all()
            logger.info("Database initialized")
            
            # Добавляем тестовые товары если БД пуста
            if Product.query.count() == 0:
                test_products = [
                    Product(name="Холодильник Samsung", category="Холодильники", price=45000, stock=10),
                    Product(name="Стиральная машина LG", category="Стиральные машины", price=35000, stock=15),
                    Product(name="Микроволновка Panasonic", category="Микроволновые печи", price=12000, stock=20),
                    Product(name="Пылесос Philips", category="Пылесосы", price=18000, stock=8),
                ]
                for p in test_products:
                    db.session.add(p)
                db.session.commit()
                logger.info("Test products added")
        except Exception as e:
            logger.error(f"DB init error: {e}")

init_db()

# Для Vercel
application = app

if __name__ == '__main__':
    app.run(debug=True)
