# app.py - полная рабочая версия для Vercel

from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os
import uuid
from functools import wraps
from werkzeug.utils import secure_filename
import logging

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_secure_2026')

# Настройка логов для отладки
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Настройка БД
database_url = os.environ.get('DATABASE_URL', '')
if not database_url:
    logger.warning("DATABASE_URL not set, using SQLite fallback")
    database_url = 'sqlite:////tmp/app.db'

if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://')

# Оптимизированные настройки для Vercel serverless
app.config.update(
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'pool_size': 1,
        'max_overflow': 0,
        'pool_timeout': 30,
    },
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    UPLOAD_FOLDER='/tmp/uploads',
)

# Создаем папку для загрузок
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
DEFAULT_IMAGE = "default.png"
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

db = SQLAlchemy(app)

# ==================== МОДЕЛИ ====================

class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(100))
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0)
    description = db.Column(db.Text)
    image = db.Column(db.String(200), default=DEFAULT_IMAGE)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(200), nullable=False)
    customer_phone = db.Column(db.String(50))
    customer_address = db.Column(db.Text)
    total = db.Column(db.Float, default=0)
    status = db.Column(db.String(50), default='new')
    date = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('OrderItem', backref='order', lazy=True, cascade='all, delete-orphan')

class OrderItem(db.Model):
    __tablename__ = 'order_items'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    product_name = db.Column(db.String(200))
    price = db.Column(db.Float)
    quantity = db.Column(db.Integer, default=1)
    product = db.relationship('Product')

# ==================== УТИЛИТЫ ====================

@app.teardown_appcontext
def shutdown_session(exception=None):
    if exception:
        db.session.rollback()
    db.session.remove()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def get_cart():
    return session.get('cart', {})

def save_cart(cart):
    session['cart'] = cart

def get_cart_items():
    cart = get_cart()
    items = []
    total = 0
    for pid, qty in cart.items():
        product = Product.query.get(int(pid))
        if product:
            subtotal = product.price * qty
            items.append({
                'product': product,
                'qty': qty,
                'subtotal': subtotal
            })
            total += subtotal
    return items, total

# ==================== МАРШРУТЫ ====================

@app.route('/')
def index():
    try:
        products = Product.query.limit(8).all()
        return render_template('index.html', products=products)
    except Exception as e:
        logger.error(f"Index error: {str(e)}")
        return render_template('index.html', products=[]), 500

@app.route('/products')
def products():
    try:
        search = request.args.get('search', '')
        category = request.args.get('category', '')
        
        query = Product.query
        if search:
            query = query.filter(Product.name.ilike(f'%{search}%'))
        if category:
            query = query.filter_by(category=category)
        
        products = query.all()
        categories = db.session.query(Product.category).distinct().all()
        
        return render_template('products.html', 
                             products=products, 
                             categories=categories,
                             search=search,
                             current_category=category)
    except Exception as e:
        logger.error(f"Products error: {str(e)}")
        flash('Ошибка загрузки товаров', 'error')
        return render_template('products.html', products=[], categories=[])

@app.route('/add_to_cart/<int:product_id>')
def add_to_cart(product_id):
    try:
        product = Product.query.get_or_404(product_id)
        cart = get_cart()
        pid = str(product_id)
        
        if pid in cart:
            cart[pid] += 1
        else:
            cart[pid] = 1
        
        save_cart(cart)
        flash(f'{product.name} добавлен в корзину', 'success')
    except Exception as e:
        logger.error(f"Add to cart error: {str(e)}")
        flash('Ошибка добавления в корзину', 'error')
    
    return redirect(request.referrer or url_for('index'))

@app.route('/remove_from_cart/<int:product_id>')
def remove_from_cart(product_id):
    try:
        cart = get_cart()
        pid = str(product_id)
        if pid in cart:
            del cart[pid]
            save_cart(cart)
    except Exception as e:
        logger.error(f"Remove from cart error: {str(e)}")
    return redirect(url_for('cart'))

@app.route('/update_cart', methods=['POST'])
def update_cart():
    try:
        cart = get_cart()
        for key, value in request.form.items():
            if key.startswith('qty_'):
                pid = key.replace('qty_', '')
                qty = int(value)
                if qty > 0:
                    cart[pid] = qty
                else:
                    cart.pop(pid, None)
        save_cart(cart)
    except Exception as e:
        logger.error(f"Update cart error: {str(e)}")
    return redirect(url_for('cart'))

@app.route('/cart')
def cart():
    try:
        items, total = get_cart_items()
        return render_template('cart.html', items=items, total=total)
    except Exception as e:
        logger.error(f"Cart error: {str(e)}")
        return render_template('cart.html', items=[], total=0)

@app.route('/checkout', methods=['POST'])
def checkout():
    try:
        items, total = get_cart_items()
        if not items:
            flash('Корзина пуста', 'error')
            return redirect(url_for('cart'))
        
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        address = request.form.get('address', '').strip()
        
        if not name or not phone or not address:
            flash('Заполните все поля', 'error')
            return redirect(url_for('cart'))
        
        order = Order(
            customer_name=name,
            customer_phone=phone,
            customer_address=address,
            total=total,
            status='new'
        )
        db.session.add(order)
        db.session.flush()
        
        for item in items:
            order_item = OrderItem(
                order_id=order.id,
                product_id=item['product'].id,
                product_name=item['product'].name,
                price=item['product'].price,
                quantity=item['qty']
            )
            db.session.add(order_item)
            
            # Уменьшаем остаток
            item['product'].stock -= item['qty']
        
        db.session.commit()
        session.pop('cart', None)
        flash('Заказ успешно оформлен!', 'success')
        return redirect(url_for('index'))
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Checkout error: {str(e)}")
        flash('Ошибка оформления заказа', 'error')
        return redirect(url_for('cart'))

# ==================== АДМИНКА ====================

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin'))
        else:
            flash('Неверный пароль', 'error')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('index'))

@app.route('/admin')
@login_required
def admin():
    try:
        products = Product.query.all()
        orders = Order.query.order_by(Order.date.desc()).all()
        return render_template('admin.html', products=products, orders=orders)
    except Exception as e:
        logger.error(f"Admin error: {str(e)}")
        flash('Ошибка загрузки админ-панели', 'error')
        return render_template('admin.html', products=[], orders=[])

@app.route('/admin/product/add', methods=['GET', 'POST'])
@login_required
def admin_product_add():
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            category = request.form.get('category', '').strip()
            price = float(request.form.get('price', 0))
            stock = int(request.form.get('stock', 0) or 0)
            description = request.form.get('description', '').strip()
            image = request.files.get('image')
            image_filename = DEFAULT_IMAGE
            
            if image and allowed_file(image.filename):
                filename = secure_filename(image.filename)
                ext = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"{uuid.uuid4().hex}.{ext}"
                save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                image.save(save_path)
                logger.info(f"Image saved to {save_path}")
                image_filename = unique_filename
            
            product = Product(
                name=name,
                category=category,
                price=price,
                stock=stock,
                description=description,
                image=image_filename
            )
            db.session.add(product)
            db.session.commit()
            flash('Товар добавлен', 'success')
            return redirect(url_for('admin'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Product add error: {str(e)}")
            flash(f'Ошибка: {str(e)}', 'error')
            return redirect(url_for('admin'))
    
    return render_template('product_form.html', title='Добавить товар', product=None)

@app.route('/admin/product/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def admin_product_edit(id):
    product = Product.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            product.name = request.form.get('name', '').strip()
            product.category = request.form.get('category', '').strip()
            product.price = float(request.form.get('price', 0))
            product.stock = int(request.form.get('stock', 0) or 0)
            product.description = request.form.get('description', '').strip()
            
            image = request.files.get('image')
            if image and allowed_file(image.filename):
                filename = secure_filename(image.filename)
                ext = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"{uuid.uuid4().hex}.{ext}"
                save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                image.save(save_path)
                product.image = unique_filename
            
            db.session.commit()
            flash('Товар обновлен', 'success')
            return redirect(url_for('admin'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Product edit error: {str(e)}")
            flash(f'Ошибка: {str(e)}', 'error')
            return redirect(url_for('admin'))
    
    return render_template('product_form.html', title='Редактировать товар', product=product)

@app.route('/admin/product/delete/<int:id>')
@login_required
def admin_product_delete(id):
    try:
        product = Product.query.get_or_404(id)
        db.session.delete(product)
        db.session.commit()
        flash('Товар удален', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Product delete error: {str(e)}")
        flash('Ошибка удаления товара', 'error')
    return redirect(url_for('admin'))

@app.route('/admin/order/status/<int:id>', methods=['POST'])
@login_required
def admin_order_status(id):
    try:
        order = Order.query.get_or_404(id)
        order.status = request.form.get('status', 'new')
        db.session.commit()
        flash('Статус обновлен', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Order status error: {str(e)}")
        flash('Ошибка обновления статуса', 'error')
    return redirect(url_for('admin'))

@app.route('/admin/order/delete/<int:id>')
@login_required
def admin_order_delete(id):
    try:
        order = Order.query.get_or_404(id)
        db.session.delete(order)
        db.session.commit()
        flash('Заказ удален', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Order delete error: {str(e)}")
        flash('Ошибка удаления заказа', 'error')
    return redirect(url_for('admin'))

@app.route('/static/uploads/<filename>')
def uploaded_file(filename):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.exists(filepath):
        return send_from_directory('static', 'default.png')
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"500 error: {str(error)}")
    return "Internal server error", 500

@app.errorhandler(404)
def not_found(error):
    return "Page not found", 404

# Инициализация БД
with app.app_context():
    try:
        db.create_all()
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Database initialization error: {str(e)}")

# Для Vercel
application = app

if __name__ == '__main__':
    app.run(debug=False)
